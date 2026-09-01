"""
Token Economics Service — meter every model call.

Non-negotiable constraint #8: Every model call is metered. No agent may call a
model provider through an unmetered path.

Uses stdlib sqlite3 wrapped with asyncio for async compatibility.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from verdity.async_sqlite import AsyncConnection

# ── Price tables (per 1M tokens) — update when models change ─────────

_PRICE_TABLE: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-haiku-3-5-20241022": {"input": 0.25, "output": 1.25},
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    "deepseek-coder": {"input": 0.14, "output": 0.28},
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return estimated USD cost for a single model call."""
    family = model.split("/")[0] if "/" in model else model
    for key, prices in _PRICE_TABLE.items():
        if key in family.lower():
            in_cost = (input_tokens / 1_000_000) * prices["input"]
            out_cost = (output_tokens / 1_000_000) * prices["output"]
            return round(in_cost + out_cost, 6)
    return round((input_tokens + output_tokens) / 1_000_000 * 5.0, 6)


class TokenEconomicsService:
    """
    Metering and budget-enforcement service.
    Backed by SQLite for dev; swappable for Prometheus/Timescale in prod.
    """

    CREATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS token_meter (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            review_run_id TEXT NOT NULL,
            agent_name    TEXT NOT NULL,
            model         TEXT NOT NULL,
            input_tokens  INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            cost_usd      REAL NOT NULL,
            repo_owner    TEXT,
            repo_name     TEXT,
            org           TEXT,
            metadata      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_run     ON token_meter(review_run_id);
        CREATE INDEX IF NOT EXISTS idx_repo    ON token_meter(repo_owner, repo_name);
        CREATE INDEX IF NOT EXISTS idx_org     ON token_meter(org);
        CREATE INDEX IF NOT EXISTS idx_recorded ON token_meter(recorded_at);
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

    async def record_call(
        self,
        *,
        review_run_id: uuid.UUID,
        agent_name: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        repo_owner: str | None = None,
        repo_name: str | None = None,
        org: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record a single model call with full tagging for spend attribution."""
        cost = estimate_cost(model, input_tokens, output_tokens)
        if self._conn is None:
            raise RuntimeError("TokenEconomicsService is not connected. Call connect() first.")
        await self._conn.execute(
            """
            INSERT INTO token_meter
                (review_run_id, agent_name, model, input_tokens, output_tokens,
                 cost_usd, repo_owner, repo_name, org, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(review_run_id),
                agent_name,
                model,
                input_tokens,
                output_tokens,
                cost,
                repo_owner,
                repo_name,
                org,
                json.dumps(metadata, default=str) if metadata else None,
            ),
        )
        await self._conn.commit()

    async def get_spend(
        self,
        *,
        repo_owner: str | None = None,
        repo_name: str | None = None,
        org: str | None = None,
        since: datetime | None = None,
    ) -> dict[str, Any]:
        """Return aggregated spend for the given scope."""
        if self._conn is None:
            raise RuntimeError("TokenEconomicsService is not connected. Call connect() first.")
        conditions: list[str] = []
        params: list[Any] = []
        if repo_owner:
            conditions.append("repo_owner = ?")
            params.append(repo_owner)
        if repo_name:
            conditions.append("repo_name = ?")
            params.append(repo_name)
        if org:
            conditions.append("org = ?")
            params.append(org)
        if since:
            conditions.append("recorded_at >= ?")
            params.append(since.isoformat())

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = await self._conn.execute(
            f"""
            SELECT
                COALESCE(SUM(cost_usd), 0.0)  AS total_cost_usd,
                SUM(input_tokens)             AS total_input_tokens,
                SUM(output_tokens)            AS total_output_tokens,
                COUNT(*)                      AS total_calls
            FROM token_meter{where}
            """,
            params,
        )
        row = rows[0] if rows else {}
        return {
            "spend_usd": round(float(row.get("total_cost_usd", 0)), 6),
            "tokens_in": int(row.get("total_input_tokens") or 0),
            "tokens_out": int(row.get("total_output_tokens") or 0),
            "total_calls": int(row.get("total_calls") or 0),
        }

    async def check_budget_enforcement(
        self,
        *,
        repo_owner: str,
        repo_name: str,
        budget_usd: float,
    ) -> dict[str, Any]:
        """
        Check whether spend has hit the configured budget cap.
        Returns: {"within_budget": bool, "spend_usd": float, "budget_usd": float,
                   "degrade_signal": str | None}
        """
        if budget_usd <= 0:
            return {
                "within_budget": True,
                "spend_usd": 0.0,
                "budget_usd": budget_usd,
                "degrade_signal": None,
            }

        stats = await self.get_spend(repo_owner=repo_owner, repo_name=repo_name)
        spend = stats["spend_usd"]
        ratio = spend / budget_usd if budget_usd > 0 else 0.0

        if ratio >= 1.0:
            signal = "halt"
        elif ratio >= 0.8:
            signal = "warn"
        elif ratio >= 0.6:
            signal = "degrade_optional"
        else:
            signal = None

        return {
            "within_budget": signal != "halt",
            "spend_usd": spend,
            "budget_usd": budget_usd,
            "ratio": round(ratio, 4),
            "degrade_signal": signal,
        }

    async def get_spend_by_org(self) -> list[dict[str, Any]]:
        """Return spend aggregated per org."""
        if self._conn is None:
            raise RuntimeError("TokenEconomicsService is not connected. Call connect() first.")
        rows = await self._conn.execute(
            """
            SELECT org,
                   COALESCE(SUM(cost_usd), 0.0)  AS spend_usd,
                   COUNT(*)                      AS total_calls,
                   SUM(input_tokens)             AS total_input_tokens,
                   SUM(output_tokens)            AS total_output_tokens
            FROM token_meter
            WHERE org IS NOT NULL AND org != ''
            GROUP BY org
            ORDER BY spend_usd DESC
            """
        )
        return [
            {
                "org": r["org"],
                "spend_usd": round(float(r["spend_usd"]), 6),
                "total_calls": int(r["total_calls"]),
                "total_input_tokens": int(r["total_input_tokens"] or 0),
                "total_output_tokens": int(r["total_output_tokens"] or 0),
            }
            for r in rows
        ]

    async def get_spend_by_repo(self) -> list[dict[str, Any]]:
        """Return spend aggregated per repo."""
        if self._conn is None:
            raise RuntimeError("TokenEconomicsService is not connected. Call connect() first.")
        rows = await self._conn.execute(
            """
            SELECT repo_owner, repo_name,
                   COALESCE(SUM(cost_usd), 0.0)  AS spend_usd,
                   COUNT(*)                      AS total_calls,
                   SUM(input_tokens)             AS total_input_tokens,
                   SUM(output_tokens)            AS total_output_tokens
            FROM token_meter
            WHERE repo_owner IS NOT NULL AND repo_name IS NOT NULL
            GROUP BY repo_owner, repo_name
            ORDER BY spend_usd DESC
            LIMIT 50
            """
        )
        return [
            {
                "repo": f"{r['repo_owner']}/{r['repo_name']}",
                "owner": r["repo_owner"],
                "name": r["repo_name"],
                "spend_usd": round(float(r["spend_usd"]), 6),
                "total_calls": int(r["total_calls"]),
                "total_input_tokens": int(r["total_input_tokens"] or 0),
                "total_output_tokens": int(r["total_output_tokens"] or 0),
            }
            for r in rows
        ]
