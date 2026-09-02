"""
Metrics Store — append-only engineering analytics for Verdity.

Non-negotiable constraint #14: No UPDATE or DELETE on review_metrics,
finding_outcomes, or review_timings tables.

Backed by SQLite for dev; swappable for Timescale/Prometheus in prod.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from verdity.async_sqlite import AsyncConnection


class MetricsStore:
    """
    Append-only metrics store for engineering analytics.

    Tracks:
      - review_metrics: finding counts, severity distribution, costs per review
      - finding_outcomes: human decisions (confirmed/false_positive/wont_fix/auto_fixed)
      - review_timings: phase-level duration tracking
    """

    CREATE_TABLES_SQL = """
        CREATE TABLE IF NOT EXISTS review_metrics (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            repo_id       TEXT NOT NULL,
            pr_number     INTEGER NOT NULL,
            metric_type   TEXT NOT NULL,
            metric_key    TEXT NOT NULL,
            metric_value  REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rm_repo ON review_metrics(repo_id);
        CREATE INDEX IF NOT EXISTS idx_rm_pr   ON review_metrics(pr_number);
        CREATE INDEX IF NOT EXISTS idx_rm_type ON review_metrics(metric_type);
        CREATE INDEX IF NOT EXISTS idx_rm_rec  ON review_metrics(recorded_at);

        CREATE TABLE IF NOT EXISTS finding_outcomes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            finding_id    TEXT NOT NULL,
            repo_id       TEXT NOT NULL,
            pr_number     INTEGER,
            final_outcome TEXT NOT NULL,
            confidence    REAL,
            severity      TEXT,
            concern       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_fo_finding ON finding_outcomes(finding_id);
        CREATE INDEX IF NOT EXISTS idx_fo_repo    ON finding_outcomes(repo_id);
        CREATE INDEX IF NOT EXISTS idx_fo_outcome ON finding_outcomes(final_outcome);

        CREATE TABLE IF NOT EXISTS review_timings (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            repo_id       TEXT NOT NULL,
            pr_number     INTEGER NOT NULL,
            phase         TEXT NOT NULL,
            duration_ms   REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rt_repo ON review_timings(repo_id);
        CREATE INDEX IF NOT EXISTS idx_rt_pr   ON review_timings(pr_number);
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn: AsyncConnection | None = None

    async def connect(self) -> None:
        self._conn = AsyncConnection(self._db_path)
        await self._conn.connect()
        await self._conn.executescript(self.CREATE_TABLES_SQL)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    # ── Recording methods (append-only) ────────────────────────────────

    async def record_review_metrics(
        self,
        *,
        repo_id: str,
        pr_number: int,
        metrics: dict[str, float],
    ) -> None:
        """Record metric key-value pairs for a review run.

        Args:
            repo_id: owner/name identifier
            pr_number: PR or MR number
            metrics: dict of metric_key → metric_value
                     e.g. {"finding_count": 5.0, "severity_critical": 1.0, "cost_usd": 0.12}
        """
        if self._conn is None:
            raise RuntimeError("MetricsStore is not connected. Call connect() first.")
        for key, value in metrics.items():
            metric_type = key.split("_")[0] if "_" in key else key
            await self._conn.execute(
                """
                INSERT INTO review_metrics (repo_id, pr_number, metric_type, metric_key, metric_value)
                VALUES (?, ?, ?, ?, ?)
                """,
                (repo_id, pr_number, metric_type, key, float(value)),
            )
        await self._conn.commit()

    async def record_finding_outcome(
        self,
        *,
        finding_id: str,
        repo_id: str,
        pr_number: int | None = None,
        final_outcome: str,
        confidence: float | None = None,
        severity: str | None = None,
        concern: str | None = None,
    ) -> None:
        """Record a human decision on a finding.

        Args:
            finding_id: UUID of the finding
            repo_id: owner/name identifier
            pr_number: PR or MR number (optional)
            final_outcome: one of confirmed, false_positive, wont_fix, auto_fixed
            confidence: original confidence score at time of decision
            severity: severity level at time of decision
            concern: concern type at time of decision
        """
        if self._conn is None:
            raise RuntimeError("MetricsStore is not connected. Call connect() first.")
        valid_outcomes = {"confirmed", "false_positive", "wont_fix", "auto_fixed"}
        if final_outcome not in valid_outcomes:
            raise ValueError(f"Invalid outcome: {final_outcome!r}. Must be one of {valid_outcomes}")
        await self._conn.execute(
            """
            INSERT INTO finding_outcomes
                (finding_id, repo_id, pr_number, final_outcome, confidence, severity, concern)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (finding_id, repo_id, pr_number, final_outcome, confidence, severity, concern),
        )
        await self._conn.commit()

    async def record_review_timing(
        self,
        *,
        repo_id: str,
        pr_number: int,
        phase: str,
        duration_ms: float,
    ) -> None:
        """Record the duration of a review phase.

        Args:
            repo_id: owner/name identifier
            pr_number: PR or MR number
            phase: phase name (e.g. 'ingestion', 'specialists', 'aggregation', 'routing', 'total')
            duration_ms: duration in milliseconds
        """
        if self._conn is None:
            raise RuntimeError("MetricsStore is not connected. Call connect() first.")
        await self._conn.execute(
            """
            INSERT INTO review_timings (repo_id, pr_number, phase, duration_ms)
            VALUES (?, ?, ?, ?)
            """,
            (repo_id, pr_number, phase, duration_ms),
        )
        await self._conn.commit()

    # ── Query methods ──────────────────────────────────────────────────

    async def get_repo_summary(
        self,
        repo_id: str,
        days: int = 30,
    ) -> dict[str, Any]:
        """Return aggregated metrics for a repo over the given time window.

        Returns:
            {
                "repo_id": str,
                "review_count": int,
                "total_findings": float,
                "severity_distribution": {severity: count},
                "false_positive_rate": float,
                "median_time_to_review": float | None,
                "total_cost_usd": float,
                "cost_per_review": float,
                "outcome_counts": {outcome: count},
            }
        """
        if self._conn is None:
            raise RuntimeError("MetricsStore is not connected. Call connect() first.")

        cutoff = datetime.now(UTC)
        from datetime import timedelta

        cutoff = cutoff - timedelta(days=days)
        cutoff_iso = cutoff.isoformat()

        # Review count (distinct pr_numbers with metrics)
        rows = await self._conn.execute(
            "SELECT COUNT(DISTINCT pr_number) AS cnt FROM review_metrics WHERE repo_id = ? AND recorded_at >= ?",
            (repo_id, cutoff_iso),
        )
        review_count = int(rows[0]["cnt"]) if rows else 0

        # Total findings
        rows = await self._conn.execute(
            "SELECT COALESCE(SUM(metric_value), 0) AS total FROM review_metrics WHERE repo_id = ? AND metric_key = 'finding_count' AND recorded_at >= ?",
            (repo_id, cutoff_iso),
        )
        total_findings = float(rows[0]["total"]) if rows else 0.0

        # Severity distribution
        severity_rows = await self._conn.execute(
            "SELECT metric_key, SUM(metric_value) AS cnt FROM review_metrics WHERE repo_id = ? AND metric_type = 'severity' AND recorded_at >= ? GROUP BY metric_key",
            (repo_id, cutoff_iso),
        )
        severity_distribution: dict[str, float] = {}
        for r in severity_rows:
            key = r["metric_key"].replace("severity_", "")
            severity_distribution[key] = float(r["cnt"])

        # False positive rate
        outcome_rows = await self._conn.execute(
            "SELECT final_outcome, COUNT(*) AS cnt FROM finding_outcomes WHERE repo_id = ? AND recorded_at >= ? GROUP BY final_outcome",
            (repo_id, cutoff_iso),
        )
        outcome_counts: dict[str, int] = {}
        for r in outcome_rows:
            outcome_counts[r["final_outcome"]] = int(r["cnt"])

        total_outcomes = sum(outcome_counts.values())
        fp_count = outcome_counts.get("false_positive", 0)
        false_positive_rate = round(fp_count / total_outcomes, 4) if total_outcomes > 0 else 0.0

        # Median time to review
        timing_rows = await self._conn.execute(
            "SELECT duration_ms FROM review_timings WHERE repo_id = ? AND phase = 'total' AND recorded_at >= ? ORDER BY duration_ms",
            (repo_id, cutoff_iso),
        )
        median_time_to_review: float | None = None
        if timing_rows:
            durations = [float(r["duration_ms"]) for r in timing_rows]
            mid = len(durations) // 2
            if len(durations) % 2 == 0 and len(durations) > 1:
                median_time_to_review = (durations[mid - 1] + durations[mid]) / 2.0
            else:
                median_time_to_review = durations[mid]

        # Total cost
        cost_rows = await self._conn.execute(
            "SELECT COALESCE(SUM(metric_value), 0) AS total FROM review_metrics WHERE repo_id = ? AND metric_key = 'cost_usd' AND recorded_at >= ?",
            (repo_id, cutoff_iso),
        )
        total_cost_usd = float(cost_rows[0]["total"]) if cost_rows else 0.0
        cost_per_review = round(total_cost_usd / review_count, 6) if review_count > 0 else 0.0

        return {
            "repo_id": repo_id,
            "review_count": review_count,
            "total_findings": total_findings,
            "severity_distribution": severity_distribution,
            "false_positive_rate": false_positive_rate,
            "median_time_to_review": median_time_to_review,
            "total_cost_usd": total_cost_usd,
            "cost_per_review": cost_per_review,
            "outcome_counts": outcome_counts,
        }

    async def get_all_outcomes(
        self,
        repo_id: str | None = None,
        days: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return all finding outcomes (for trust calibration).

        Args:
            repo_id: optional filter by repo
            days: optional filter by time window

        Returns:
            list of dicts with keys: finding_id, repo_id, pr_number,
            final_outcome, confidence, severity, concern, recorded_at
        """
        if self._conn is None:
            raise RuntimeError("MetricsStore is not connected. Call connect() first.")

        conditions = []
        params: list[Any] = []

        if repo_id:
            conditions.append("repo_id = ?")
            params.append(repo_id)

        if days:
            from datetime import timedelta

            cutoff = datetime.now(UTC) - timedelta(days=days)
            conditions.append("recorded_at >= ?")
            params.append(cutoff.isoformat())

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        rows = await self._conn.execute(
            f"""
            SELECT finding_id, repo_id, pr_number, final_outcome,
                   confidence, severity, concern, recorded_at
            FROM finding_outcomes
            {where}
            ORDER BY recorded_at
            """,
            tuple(params),
        )

        return [dict(r) for r in rows]

    async def get_repo_dashboard(
        self,
        repo_id: str,
        days: int = 30,
    ) -> dict[str, Any]:
        """Return chart-ready data for the engineering analytics dashboard.

        Returns summary + daily breakdown for time-series visualization.
        """
        summary = await self.get_repo_summary(repo_id, days=days)

        if self._conn is None:  # pragma: no cover
            raise RuntimeError("MetricsStore is not connected. Call connect() first.")

        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(days=days)
        cutoff_iso = cutoff.isoformat()

        # Daily review counts
        daily_rows = await self._conn.execute(
            """
            SELECT DATE(recorded_at) AS day, COUNT(DISTINCT pr_number) AS reviews
            FROM review_metrics WHERE repo_id = ? AND recorded_at >= ?
            GROUP BY DATE(recorded_at) ORDER BY day
            """,
            (repo_id, cutoff_iso),
        )
        daily_reviews = [{"day": r["day"], "reviews": int(r["reviews"])} for r in daily_rows]

        # Daily cost
        daily_cost_rows = await self._conn.execute(
            """
            SELECT DATE(recorded_at) AS day, SUM(metric_value) AS cost
            FROM review_metrics WHERE repo_id = ? AND metric_key = 'cost_usd' AND recorded_at >= ?
            GROUP BY DATE(recorded_at) ORDER BY day
            """,
            (repo_id, cutoff_iso),
        )
        daily_costs = [{"day": r["day"], "cost_usd": float(r["cost"])} for r in daily_cost_rows]

        return {
            "summary": summary,
            "daily_reviews": daily_reviews,
            "daily_costs": daily_costs,
        }
