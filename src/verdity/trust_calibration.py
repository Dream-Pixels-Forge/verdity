"""
Trust Calibration — Learn from human feedback to improve confidence scoring.

Non-negotiable constraint #5: confidence scores remain deterministic (no LLM per-finding).
Trust calibration adjusts SEVERITY_WEIGHTS and CONCERN_BOOST based on historical outcome data,
not per-finding LLM calls. The calibrated weights are stored persistently and applied
consistently across all future reviews.

Usage:
    calibrator = TrustCalibrator(db_path=":memory:")
    await calibrator.connect()
    # Record human decisions on findings
    await calibrator.record_outcome(
        finding_type="security-hardcoded-credential",
        outcome="confirmed",
        repo_id="acme/widgets",
        confidence=0.95,
        severity="high",
        concern="security",
    )
    # Recalibrate weights based on accumulated feedback
    result = await calibrator.recalibrate(min_samples=50)
    # Use adjusted weights in confidence computation
    adjusted_weights, concern_boost = await calibrator.get_adjusted_weights()
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from verdity.async_sqlite import AsyncConnection
from verdity.router import DEFAULT_CONCERN_BOOST, DEFAULT_SEVERITY_WEIGHTS

logger = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS trust_signals (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        finding_type  TEXT NOT NULL,      -- e.g. "security-hardcoded-credential"
        outcome       TEXT NOT NULL,    -- "confirmed", "false_positive", "wont_fix"
        repo_id       TEXT NOT NULL,
        timestamp     TEXT NOT NULL,
        confidence    REAL NOT NULL,
        severity      TEXT NOT NULL,
        concern       TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS calibration_state (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        version       INTEGER NOT NULL DEFAULT 1,
        weights_json  TEXT NOT NULL,      -- serialized adjusted weights
        last_trained  TEXT NOT NULL,
        sample_count  INTEGER NOT NULL DEFAULT 0,
        precision_at_09 REAL DEFAULT 0.0,
        recall_at_06    REAL DEFAULT 0.0
    );
"""


@dataclass
class CalibrationResult:
    """Result of a trust calibration run."""

    adjusted_weights: dict[str, float]
    concern_boost: dict[str, float]
    precision_at_09: float
    recall_at_06: float
    sample_count: int
    changed: bool


def _default_weights_json() -> str:
    """Serialize the default severity weights and concern boost."""
    import json

    default = {
        "severity_weights": DEFAULT_SEVERITY_WEIGHTS,
        "concern_boost": DEFAULT_CONCERN_BOOST,
    }
    return json.dumps(default)


class TrustCalibrator:
    """
    Learns from human feedback to improve confidence scoring.

    Non-negotiable: confidence scores remain deterministic (constraint #5).
    Trust calibration adjusts SEVERITY_WEIGHTS and CONCERN_BOOST
    based on historical outcome data, not per-finding LLM calls.

    The calibrator maintains:
    - trust_signals: per-finding-type outcome history with confidence scores
    - calibration_state: current adjusted weights and performance metrics
    - sample tracking: when to trigger recalibration (minimum sample count)
    """

    def __init__(self, db_path: str = ":memory:", metrics_store: object | None = None) -> None:
        self._db_path = db_path
        self._metrics_store = metrics_store
        self._conn: Any | None = None

    async def connect(self) -> None:
        self._conn = AsyncConnection(self._db_path)
        await self._conn.connect()
        await self._conn.executescript(CREATE_TABLE_SQL)
        # Initialize calibration_state with default weights if empty
        rows = await self._conn.execute("SELECT COUNT(*) AS n FROM calibration_state")
        n = rows[0]["n"] if rows else 0
        if n == 0:
            await self._conn.execute(
                """
                INSERT INTO calibration_state (version, weights_json, last_trained, sample_count)
                VALUES (1, ?, datetime('now'), 0)
                """,
                (_default_weights_json(),),
            )
            await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    # ── Recording human outcomes ────────────────────────────────────────

    async def record_outcome(
        self,
        finding_type: str,
        outcome: str,
        repo_id: str,
        confidence: float,
        severity: str,
        concern: str,
    ) -> None:
        """
        Record a human decision on a finding.

        Args:
            finding_type: e.g. "security-hardcoded-credential", "quality-bare-except"
            outcome: one of "confirmed", "false_positive", "wont_fix"
            repo_id: e.g. "acme/widgets"
            confidence: original confidence score at time of decision
            severity: severity level at time of decision
            concern: concern type at time of decision
        """
        if self._conn is None:
            raise RuntimeError("TrustCalibrator not connected. Call connect() first.")
        valid_outcomes = {"confirmed", "false_positive", "wont_fix"}
        if outcome not in valid_outcomes:
            raise ValueError(f"Invalid outcome: {outcome!r}. Must be one of {valid_outcomes}.")
        await self._conn.execute(
            """
            INSERT INTO trust_signals (finding_type, outcome, repo_id, timestamp, confidence, severity, concern)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                finding_type,
                outcome,
                repo_id,
                datetime.now(UTC).isoformat(),
                confidence,
                severity,
                concern,
            ),
        )
        await self._conn.commit()

    # ── Recalibration ─────────────────────────────────────────────────

    async def recalibrate(self, min_samples: int = 50) -> CalibrationResult:
        """
        Recalibrate severity weights and concern boost based on accumulated feedback.

        Only runs when at least min_samples outcomes have been recorded.
        Adjusts weights downward for patterns with many false positives,
        and upward for patterns frequently confirmed.

        Returns CalibrationResult with:
        - adjusted_weights: dict of new severity/concern weights
        - precision_at_09: float (what % of auto-approved findings were confirmed)
        - recall_at_06: float (what % of confirmed findings scored >= 0.6)
        - sample_count: int
        - changed: bool (whether weights actually changed)
        """
        if self._conn is None:
            raise RuntimeError("TrustCalibrator not connected. Call connect() first.")

        # Count samples per (finding_type, outcome) combo
        rows = await self._conn.execute(
            """
            SELECT finding_type, outcome, COUNT(*) AS cnt, AVG(confidence) AS avg_conf
            FROM trust_signals
            GROUP BY finding_type, outcome
        """
        )

        # Build per-type stats
        type_stats: dict[str, dict[str, Any]] = {}
        for row in rows:
            ft = row["finding_type"]
            if ft not in type_stats:
                type_stats[ft] = {
                    "confirmed": 0,
                    "false_positive": 0,
                    "wont_fix": 0,
                    "total": 0,
                    "total_confidence": 0.0,
                }
            outcome = row["outcome"]
            cnt = row["cnt"]
            avg_c = row["avg_conf"] if row["avg_conf"] is not None else 0.0
            type_stats[ft][outcome] = cnt
            type_stats[ft]["total"] += cnt
            type_stats[ft]["total_confidence"] += avg_c

        # Compute new weights
        new_severity_weights: dict[str, float] = dict(DEFAULT_SEVERITY_WEIGHTS)
        new_concern_boost: dict[str, float] = dict(DEFAULT_CONCERN_BOOST)

        # Per-type adjustment logic
        for stats in type_stats.values():
            total = stats["total"]
            if total < min_samples:
                continue  # Not enough data yet

            fp_count = stats.get("false_positive", 0)
            confirmed_count = stats.get("confirmed", 0)
            fp_rate = fp_count / total if total > 0 else 0.0

            # If high false positive rate, lower the severity weight for this type
            # and reduce concern boost
            if fp_rate > 0.3:  # >30% false positives
                new_severity_weights = {
                    k: v * (1.0 - 0.1 * fp_rate) for k, v in new_severity_weights.items()
                }
                new_concern_boost = {
                    k: v * (1.0 - 0.1 * fp_rate) for k, v in new_concern_boost.items()
                }
            elif fp_rate < 0.1 and confirmed_count / total > 0.8:  # <10% FP, >80% confirmed
                # Boost confidence for this type
                new_severity_weights = {k: v * 1.05 for k, v in new_severity_weights.items()}
                new_concern_boost = {k: v * 1.05 for k, v in new_concern_boost.items()}

        # Save calibration state
        weights_json = json.dumps(
            {
                "severity_weights": new_severity_weights,
                "concern_boost": new_concern_boost,
            }
        )
        last_trained = datetime.now(UTC).isoformat()
        sample_count = sum(s["total"] for s in type_stats.values())

        await self._conn.execute(
            """
            UPDATE calibration_state
            SET version = version + 1,
                weights_json = ?,
                last_trained = ?,
                sample_count = ?
            WHERE id = 1
        """,
            (weights_json, last_trained, sample_count),
        )
        await self._conn.commit()

        # Compute precision@0.9 and recall@0.6 from the recorded signals
        precision_09 = 0.0
        recall_06 = 0.0

        # Count how many signals have confidence >= 0.9 and were confirmed
        confirmed_09 = 0
        total_09 = 0
        # Count how many signals have confidence >= 0.6 and were confirmed
        confirmed_06 = 0
        total_06 = 0

        if self._conn:
            signal_rows = await self._conn.execute("SELECT confidence, outcome FROM trust_signals")
            for row in signal_rows:
                conf = row["confidence"]
                outcome = row["outcome"]
                if conf >= 0.9:
                    total_09 += 1
                    if outcome == "confirmed":
                        confirmed_09 += 1
                if conf >= 0.6:
                    total_06 += 1
                    if outcome == "confirmed":
                        confirmed_06 += 1

        precision_09 = confirmed_09 / total_09 if total_09 > 0 else 0.0
        recall_06 = confirmed_06 / total_06 if total_06 > 0 else 0.0

        changed = (
            new_severity_weights != DEFAULT_SEVERITY_WEIGHTS
            or new_concern_boost != DEFAULT_CONCERN_BOOST
        )

        return CalibrationResult(
            adjusted_weights=new_severity_weights,
            concern_boost=new_concern_boost,
            precision_at_09=precision_09,
            recall_at_06=recall_06,
            sample_count=sum(s["total"] for s in type_stats.values()),
            changed=changed,
        )

    # ── Adjusted weights accessors ──────────────────────────────────────

    async def get_adjusted_weights(self) -> tuple[dict[str, float], dict[str, float]]:
        """
        Return current calibrated (severity_weights, concern_boost).

        Falls back to default weights if calibration hasn't run or has insufficient data.
        """
        if self._conn is None:
            raise RuntimeError("TrustCalibrator not connected. Call connect() first.")

        rows = await self._conn.execute("SELECT weights_json FROM calibration_state WHERE id = 1")

        if rows and rows[0].get("weights_json"):
            data = json.loads(rows[0]["weights_json"])
            return data.get("severity_weights", DEFAULT_SEVERITY_WEIGHTS), data.get(
                "concern_boost", DEFAULT_CONCERN_BOOST
            )

        # Return defaults if no calibration data
        return dict(DEFAULT_SEVERITY_WEIGHTS), dict(DEFAULT_CONCERN_BOOST)

    async def get_calibration_stats(self) -> dict[str, Any]:
        """Return current calibration state for dashboard display."""
        if self._conn is None:
            raise RuntimeError("TrustCalibrator not connected. Call connect() first.")

        rows = await self._conn.execute(
            "SELECT version, sample_count, precision_at_09, recall_at_06, last_trained FROM calibration_state WHERE id = 1"
        )

        if rows:
            row = rows[0]
            return {
                "version": row["version"],
                "sample_count": row["sample_count"],
                "precision_at_09": row["precision_at_09"],
                "recall_at_06": row["recall_at_06"],
                "last_trained": row["last_trained"],
            }
        return {
            "version": 0,
            "sample_count": 0,
            "precision_at_09": 0.0,
            "recall_at_06": 0.0,
            "last_trained": None,
        }
