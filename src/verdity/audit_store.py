"""
Append-only Audit Store.

Every finding, every routing decision, every approval-queue action, and every
model call is logged here. This backs FR-11 (Full audit log).

Uses stdlib sqlite3 wrapped with asyncio.to_thread for async compatibility.
No external dependencies beyond what's in the Python stdlib.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from verdity.async_sqlite import AsyncConnection


class AuditStore:
    """
    Append-only audit log backed by SQLite.
    Every write is a real INSERT with a transaction — no soft deletes, no updates.
    """

    CREATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS audit_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            event_type    TEXT NOT NULL,
            entity_type   TEXT NOT NULL,
            entity_id     TEXT NOT NULL,
            related_run_id TEXT,
            payload       TEXT NOT NULL,
            checksum      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_event_type    ON audit_log(event_type);
        CREATE INDEX IF NOT EXISTS idx_entity        ON audit_log(entity_type, entity_id);
        CREATE INDEX IF NOT EXISTS idx_related_run   ON audit_log(related_run_id);
        CREATE INDEX IF NOT EXISTS idx_logged_at     ON audit_log(logged_at);
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

    async def append(
        self,
        event_type: str,
        entity_type: str,
        entity_id: str,
        payload: Any,
        related_run_id: uuid.UUID | None = None,
    ) -> int:
        """Append a single audit record. Returns the row id."""
        payload_json = json.dumps(payload, sort_keys=True, default=str)
        checksum = hashlib.sha256(payload_json.encode()).hexdigest()

        if self._conn is None:
            raise RuntimeError("AuditStore is not connected. Call connect() first.")
        await self._conn.execute(
            """
            INSERT INTO audit_log
                (event_type, entity_type, entity_id, related_run_id, payload, checksum)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_type,
                entity_type,
                entity_id,
                str(related_run_id) if related_run_id else None,
                payload_json,
                checksum,
            ),
        )
        await self._conn.commit()
        # Return last row id
        rows = await self._conn.execute("SELECT last_insert_rowid() as rid")
        return int(rows[0]["rid"]) if rows else 0

    async def query_by_run(
        self,
        review_run_id: uuid.UUID,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query audit records for a given review run, ordered by time."""
        if self._conn is None:
            raise RuntimeError("AuditStore is not connected. Call connect() first.")
        rows = await self._conn.execute(
            """
            SELECT id, logged_at, event_type, entity_type, entity_id, payload
            FROM audit_log
            WHERE related_run_id = ?
            ORDER BY logged_at ASC
            LIMIT ? OFFSET ?
            """,
            (str(review_run_id), limit, offset),
        )
        return rows

    async def query_by_entity(
        self,
        entity_type: str,
        entity_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query audit records for a specific entity."""
        if self._conn is None:
            raise RuntimeError("AuditStore is not connected. Call connect() first.")
        rows = await self._conn.execute(
            """
            SELECT id, logged_at, event_type, entity_type, entity_id, payload
            FROM audit_log
            WHERE entity_type = ? AND entity_id = ?
            ORDER BY logged_at ASC
            LIMIT ?
            """,
            (entity_type, entity_id, limit),
        )
        return rows
