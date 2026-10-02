"""
Approval Queue Store.

Persistent store for findings awaiting human review.
SQLite-backed, partitioned by repo_id.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from verdity.async_sqlite import AsyncConnection

logger = logging.getLogger(__name__)


@dataclass
class ApprovalItem:
    """An item in the approval queue."""

    id: str
    repo_id: str
    pr_number: int
    finding_id: str
    reason: str
    sla_hours: int = 24
    created_at: datetime | None = None
    escalated: bool = False


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
    created_at  TEXT NOT NULL,
    sla_hours   INTEGER NOT NULL DEFAULT 24,
    escalated   INTEGER NOT NULL DEFAULT 0
);
"""

_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_queue_run ON approval_queue(run_id);
CREATE INDEX IF NOT EXISTS idx_queue_repo ON approval_queue(repo_id);
CREATE INDEX IF NOT EXISTS idx_queue_status ON approval_queue(status);
CREATE INDEX IF NOT EXISTS idx_queue_severity ON approval_queue(severity);
CREATE INDEX IF NOT EXISTS idx_queue_sla ON approval_queue(sla_hours, created_at);
"""


class ApprovalQueue:
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
        repo_id: str,
        concern: str,
        severity: str,
        file: str,
        line_start: int,
        summary: str,
        explanation: str | None,
        confidence: float,
        route_action: str,
        route_reason: str | None,
        sla_hours: int = 24,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO approval_queue
                (id, run_id, finding_id, repo_id, concern, severity,
                 file, line_start, summary, explanation, confidence,
                 route_action, route_reason, status, reviewer_id,
                 resolved_at, created_at, sla_hours, escalated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                sla_hours,
                0,
            ),
        )
        await self._conn.commit()

    async def add_item(self, item: ApprovalItem) -> None:
        """Add an ApprovalItem to the queue."""
        now = datetime.now(UTC).isoformat()
        # Use item.created_at if provided, otherwise use current time
        created_at = item.created_at.isoformat() if item.created_at else now
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO approval_queue
                (id, run_id, finding_id, repo_id, concern, severity,
                 file, line_start, summary, explanation, confidence,
                 route_action, route_reason, status, reviewer_id,
                 resolved_at, created_at, sla_hours, escalated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                "00000000-0000-0000-0000-000000000000",  # dummy run_id
                item.finding_id,
                item.repo_id,
                item.reason,  # using reason as concern
                "high",  # dummy severity
                item.finding_id,  # using finding_id as file
                0,  # dummy line_start
                item.reason,
                None,  # explanation
                1.0,  # dummy confidence
                "manual_review",  # route_action
                item.reason,  # route_reason
                "pending",
                None,  # reviewer_id
                None,  # resolved_at
                created_at,
                item.sla_hours,
                1 if item.escalated else 0,
            ),
        )
        await self._conn.commit()

    async def get_pending(
        self, repo_id: str | None = None, limit: int = 50
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
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "UPDATE approval_queue SET status=?, reviewer_id=?, resolved_at=? WHERE id=?",
            (action, reviewer_id, now, queue_id),
        )
        await self._conn.commit()

    async def get_item(self, item_id: str) -> dict[str, Any] | None:
        """Get an item by ID."""
        rows = await self._conn.execute(
            "SELECT * FROM approval_queue WHERE id = ?",
            (item_id,),
        )
        if not rows:
            return None
        class ApprovalItem:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)
        return ApprovalItem(**dict(rows[0]))

    async def stats(self, repo_id: str | None = None) -> dict[str, int]:
        if repo_id:
            rows = await self._conn.execute(
                "SELECT status, COUNT(*) as cnt FROM approval_queue "
                "WHERE repo_id=? GROUP BY status",
                (str(repo_id),),
            )
        else:
            rows = await self._conn.execute(
                "SELECT status, COUNT(*) as cnt FROM approval_queue GROUP BY status",
            )
        return {r["status"]: r["cnt"] for r in rows}

    async def check_sla_escalations(self) -> list[dict[str, Any]]:
        """
        Check for items past their SLA and mark them as escalated.

        Returns:
            List of escalated items
        """
        now = datetime.now(UTC)
        cutoff = now.isoformat()

        # Find pending items past their SLA
        rows = await self._conn.execute(
            """
            SELECT * FROM approval_queue
            WHERE status = 'pending'
              AND escalated = 0
              AND datetime(created_at, '+' || sla_hours || ' hours') < ?
            """,
            (cutoff,),
        )

        escalated_items = []
        for row in rows:
            # Mark as escalated
            await self._conn.execute(
                "UPDATE approval_queue SET escalated = 1 WHERE id = ?",
                (row["id"],),
            )
            # Create an object with attribute access for test compatibility
            class EscalatedItem:
                def __init__(self, **kwargs):
                    for k, v in kwargs.items():
                        setattr(self, k, v)
            escalated_items.append(EscalatedItem(**dict(row)))

        if escalated_items:
            await self._conn.commit()
            logger.info("Escalated %d items past SLA", len(escalated_items))

        return escalated_items

# Backward compatibility alias
ApprovalQueueStore = ApprovalQueue

