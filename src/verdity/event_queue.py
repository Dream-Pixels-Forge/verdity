"""
Durable Event Queue for Verdity.

Non-negotiable constraint #2: Ingestion and processing are decoupled via a
durable queue. The webhook handler never calls an LLM or hits the Semantic
Index — it only verifies, enqueues, and ACKs.

Uses stdlib sqlite3 wrapped with asyncio for async compatibility.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from verdity.async_sqlite import AsyncConnection
from verdity.schemas import QueueEnvelope, VerdityEvent


class QueueError(Exception):
    """Base exception for queue operations."""


class QueueFullError(QueueError):
    pass


class QueueNotFoundError(QueueError):
    pass


class EventQueue:
    """
    Durable, at-least-once event queue backed by SQLite.
    Partitioned by repo_id to preserve ordering per repo.
    """

    CREATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS queue_messages (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id    TEXT UNIQUE NOT NULL,
            repo_id       TEXT NOT NULL,
            envelope_json TEXT NOT NULL,
            state         TEXT NOT NULL DEFAULT 'pending',
            created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            acked_at      TEXT,
            failed_at     TEXT,
            retry_count   INTEGER NOT NULL DEFAULT 0,
            error_msg     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_repo_state ON queue_messages(repo_id, state);
        CREATE INDEX IF NOT EXISTS idx_state     ON queue_messages(state);
        CREATE INDEX IF NOT EXISTS idx_created   ON queue_messages(created_at);
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn: AsyncConnection | None = None

    async def connect(self) -> None:
        self._conn = AsyncConnection(self._db_path)
        await self._conn.connect()
        await self._conn.executescript(self.CREATE_TABLE_SQL)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def publish(self, envelope: QueueEnvelope) -> str:
        """
        Enqueue a message. Returns the message_id (derived from delivery_id).
        Idempotent: publishing the same delivery_id twice is a no-op.
        delivery_id is GitHub's per-webhook UUID — the correct dedup key.
        """
        # Use GitHub's delivery_id as the dedup key (stable, globally unique per webhook)
        msg_id = envelope.event.delivery_id
        repo_id = f"{envelope.event.repo.owner}/{envelope.event.repo.name}"
        envelope_json = json.dumps(
            {"event": envelope.event.model_dump(), "enqueued_at": envelope.enqueued_at.isoformat()},
            default=str,
        )
        if self._conn is None:
            raise RuntimeError("EventQueue is not connected. Call connect() first.")
        await self._conn.execute(
            """
            INSERT OR IGNORE INTO queue_messages
                (message_id, repo_id, envelope_json, state)
            VALUES (?, ?, ?, 'pending')
            """,
            (msg_id, repo_id, envelope_json),
        )
        await self._conn.commit()
        return msg_id

    async def consume(self, repo_id: str | None = None, timeout_ms: int = 500) -> QueueEnvelope | None:
        """
        Dequeue the oldest pending message for the given repo (or any repo if None).
        Returns None if no messages are available.
        """
        if self._conn is None:
            raise RuntimeError("EventQueue is not connected. Call connect() first.")
        target_repo = repo_id or "%"
        if repo_id:
            # Escape LIKE wildcards in repo_id to prevent cross-repo matching
            escaped_repo = repo_id.replace("%", "\\%").replace("_", "\\_")
            rows = await self._conn.execute(
                """
                SELECT id, message_id, envelope_json, retry_count
                FROM queue_messages
                WHERE state = 'pending' AND repo_id LIKE ? ESCAPE '\\'
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (escaped_repo,),
            )
        else:
            rows = await self._conn.execute(
                """
                SELECT id, message_id, envelope_json, retry_count
                FROM queue_messages
                WHERE state = 'pending'
                ORDER BY created_at ASC
                LIMIT 1
                """,
            )
        row = rows[0] if rows else None

        if row is None:
            return None

        msg_id = row["message_id"]
        # Mark as processing to prevent double-delivery
        await self._conn.execute(
            "UPDATE queue_messages SET state = 'processing' WHERE message_id = ?",
            (msg_id,),
        )
        await self._conn.commit()

        data = json.loads(row["envelope_json"])
        event_dict = data["event"]
        enqueued_at = datetime.fromisoformat(data["enqueued_at"])

        envelope = QueueEnvelope(
            event=VerdityEvent(**event_dict),
            enqueued_at=enqueued_at,
            retry_count=row["retry_count"],
        )
        return envelope

    async def acknowledge(self, message_id: str) -> None:
        """Mark a message as successfully processed."""
        if self._conn is None:
            raise RuntimeError("EventQueue is not connected. Call connect() first.")
        await self._conn.execute(
            """
            UPDATE queue_messages
            SET state = 'acked', acked_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE message_id = ? AND state = 'processing'
            """,
            (message_id,),
        )
        await self._conn.commit()

    async def nack(
        self,
        message_id: str,
        error_msg: str | None = None,
        max_retries: int = 3,
    ) -> None:
        """Reject a message. Increments retry_count; if exhausted, moves to 'dead'."""
        if self._conn is None:
            raise RuntimeError("EventQueue is not connected. Call connect() first.")
        row = await self._conn.execute_one(
            "SELECT retry_count FROM queue_messages WHERE message_id = ?",
            (message_id,),
        )
        if row is None:
            return

        new_retry = row["retry_count"] + 1
        if new_retry >= max_retries:
            await self._conn.execute(
                """
                UPDATE queue_messages
                SET state = 'dead', failed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                    error_msg = ?, retry_count = ?
                WHERE message_id = ?
                """,
                (error_msg, new_retry, message_id),
            )
        else:
            await self._conn.execute(
                """
                UPDATE queue_messages
                SET state = 'pending', retry_count = ?, error_msg = NULL
                WHERE message_id = ?
                """,
                (new_retry, message_id),
            )
        await self._conn.commit()

    async def count_by_state(self, repo_id: str | None = None) -> dict[str, int]:
        """Return counts per state for monitoring."""
        if self._conn is None:
            raise RuntimeError("EventQueue is not connected. Call connect() first.")
        conditions: list[str] = []
        params: list[Any] = []
        if repo_id:
            conditions.append("repo_id = ?")
            params.append(repo_id)
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        rows = await self._conn.execute(
            f"""
            SELECT state, COUNT(*) AS cnt
            FROM queue_messages{where}
            GROUP BY state
            """,
            params,
        )
        result = {"pending": 0, "processing": 0, "acked": 0, "dead": 0}
        for row in rows:
            result[row["state"]] = row["cnt"]
        return result
