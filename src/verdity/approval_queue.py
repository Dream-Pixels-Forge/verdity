"""
Approval Queue Store.

Persistent store for findings awaiting human review.
SQLite-backed, partitioned by repo_id.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from verdity.async_sqlite import AsyncConnection

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_queue (
    id          TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL,
    finding_id  TEXT NOT NULL,
    repo_id     TEXT NOT NULL,
    concern     TEXT NOT NULL,
    severity    TEXT NOT NULL,
    file        TEXT NOT NULL,
    line_start  INTEGER NOT NULL,
    summary     TEXT NOT NULL,
    explanation TEXT,
    confidence  REAL NOT NULL,
    route_action TEXT NOT NULL,
    route_reason TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',
    reviewer_id TEXT,
    resolved_at TIMESTAMP,
    created_at  TEXT NOT NULL
);
"""

_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_queue_run ON approval_queue(run_id);
CREATE INDEX IF NOT EXISTS idx_queue_repo ON approval_queue(repo_id);
CREATE INDEX IF NOT EXISTS idx_queue_status ON approval_queue(status);
CREATE INDEX IF NOT EXISTS idx_queue_severity ON approval_queue(severity);
"""


class ApprovalQueueStore:
    """SQLite-backed approval queue with repo partitioning."""

    def __init__(self, db_path: str = ":memory:"):
        self._db_path = db_path
        self._conn: AsyncConnection | None = None

    async def connect(self) -> None:
        self._conn = AsyncConnection(self._db_path)
        await self._conn.connect()
        await self._conn.executescript(_SCHEMA)
        await self._conn.executescript(_INDEXES)
        logger.info("ApprovalQueueStore connected to %s", self._db_path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def enqueue(
        self,
        run_id: uuid.UUID,
        finding_id: uuid.UUID,
        repo_id: int,
        concern: str,
        severity: str,
        file: str,
        line_start: int,
        summary: str,
        explanation: str | None,
        confidence: float,
        route_action: str,
        route_reason: str | None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO approval_queue
                (id, run_id, finding_id, repo_id, concern, severity,
                 file, line_start, summary, explanation, confidence,
                 route_action, route_reason, status, reviewer_id,
                 resolved_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                str(run_id),
                str(finding_id),
                str(repo_id),
                concern,
                severity,
                file,
                line_start,
                summary,
                explanation,
                confidence,
                route_action,
                route_reason,
                "pending",
                None,
                None,
                now,
            ),
        )
        await self._conn.commit()

    async def get_pending(
        self, repo_id: int | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if repo_id:
            rows = await self._conn.execute(
                "SELECT * FROM approval_queue "
                "WHERE status='pending' AND repo_id=? "
                "ORDER BY confidence DESC LIMIT ?",
                (str(repo_id), limit),
            )
        else:
            rows = await self._conn.execute(
                "SELECT * FROM approval_queue "
                "WHERE status='pending' "
                "ORDER BY confidence DESC LIMIT ?",
                (limit,),
            )
        return rows

    async def resolve(
        self, queue_id: str, reviewer_id: str, action: str, notes: str | None = None
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "UPDATE approval_queue SET status=?, reviewer_id=?, resolved_at=? WHERE id=?",
            (action, reviewer_id, now, queue_id),
        )
        await self._conn.commit()

    async def stats(self, repo_id: int | None = None) -> dict[str, int]:
        if repo_id:
            rows = await self._conn.execute(
                "SELECT status, COUNT(*) as cnt FROM approval_queue "
                "WHERE repo_id=? GROUP BY status",
                (str(repo_id),),
            )
        else:
            rows = await self._conn.execute(
                "SELECT status, COUNT(*) as cnt FROM approval_queue "
                "GROUP BY status",
            )
        return {r["status"]: r["cnt"] for r in rows}
