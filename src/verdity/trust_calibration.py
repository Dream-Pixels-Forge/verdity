"""
Trust Calibration — learns from human feedback to adjust confidence weights.

Non-negotiable constraint: TrustCalibrator adjusts weight maps (SEVERITY_WEIGHTS,
CONCERN_BOOST), never individual finding scores (preserves determinism, constraint #5).

Phase 10 of v0.4.0 build.
"""

from __future__ import annotations

import logging
from typing import Any

from verdity.metrics_store import MetricsStore

logger = logging.getLogger(__name__)

# ── Default weights (same as router.py) ─────────────────────────────

DEFAULT_SEVERITY_WEIGHTS: dict[str, float] = {
    "critical": 1.0,
    "high": 0.8,
    "medium": 0.5,
    "low": 0.3,
    "info": 0.1,
}

DEFAULT_CONCERN_BOOST: dict[str, float] = {
    "security": 0.15,
    "code_quality": 0.0,
    "testing": 0.05,
    "documentation": 0.0,
}


class TrustCalibrator:
    """Learns from human feedback to adjust confidence scoring weights.

    Reads outcome history from MetricsStore, computes precision by
    severity and concern type, and produces calibrated weight maps.

    The calibrated weights are fed into `compute_confidence()` to
    replace the static defaults — but individual finding scores are
    never post-hoc adjusted (constraint #5).
    """

    def __init__(self, metrics_store: MetricsStore) -> None:
        self._store = metrics_store
        self._calibrated_severity: dict[str, float] | None = None
        self._calibrated_concern: dict[str, float] | None = None
        self._last_sample_count: int = 0

    # ── Core API ──────────────────────────────────────────────────────

    async def record_outcome(
        self,
        *,
        finding_id: str,
        repo_id: str,
        outcome: str,
        confidence: float,
        severity: str,
        concern: str,
        pr_number: int | None = None,
    ) -> None:
        """Record a human decision on a finding.

        Delegates to MetricsStore.record_finding_outcome().
        Valid outcomes: confirmed, false_positive, wont_fix, auto_fixed.
        """
        await self._store.record_finding_outcome(
            finding_id=finding_id,
            repo_id=repo_id,
            pr_number=pr_number,
            final_outcome=outcome,
            confidence=confidence,
            severity=severity,
            concern=concern,
        )

    async def recalibrate(self, min_samples: int = 50) -> bool:
        """Recompute adjusted weights from outcome history.

        Returns True if recalibration was performed (enough samples).
        Returns False if fewer than `min_samples` outcomes exist.

        Algorithm:
          1. Fetch all finding_outcomes from MetricsStore
          2. Group by (severity, concern)
          3. Compute precision per group: confirmed / (confirmed + false_positive)
          4. Adjust severity weights: if precision < 0.8, reduce weight proportionally
          5. Adjust concern boosts: if precision < 0.8, reduce boost proportionally
        """
        outcomes = await self._store.get_all_outcomes()
        self._last_sample_count = len(outcomes)

        if len(outcomes) < min_samples:
            logger.info(
                "Trust calibration skipped: %d samples < %d minimum",
                len(outcomes),
                min_samples,
            )
            return False

        # ── Group by (severity, concern) ──────────────────────────────
        groups: dict[tuple[str, str], list[str]] = {}
        for o in outcomes:
            key = (o.get("severity", "medium"), o.get("concern", "code_quality"))
            groups.setdefault(key, []).append(o.get("final_outcome", ""))

        # ── Compute precision per group ───────────────────────────────
        group_precision: dict[tuple[str, str], float] = {}
        for key, outcomes_list in groups.items():
            confirmed = sum(1 for o in outcomes_list if o == "confirmed")
            false_pos = sum(1 for o in outcomes_list if o == "false_positive")
            relevant = confirmed + false_pos
            precision = confirmed / relevant if relevant > 0 else 0.5
            group_precision[key] = precision

        # ── Aggregate precision by severity ───────────────────────────
        severity_precision: dict[str, list[float]] = {}
        for (sev, _con), prec in group_precision.items():
            severity_precision.setdefault(sev, []).append(prec)

        # ── Aggregate precision by concern ────────────────────────────
        concern_precision: dict[str, list[float]] = {}
        for (_sev, con), prec in group_precision.items():
            concern_precision.setdefault(con, []).append(prec)

        # ── Adjust severity weights ───────────────────────────────────
        calibrated_severity = dict(DEFAULT_SEVERITY_WEIGHTS)
        for sev, precisions in severity_precision.items():
            avg_precision = sum(precisions) / len(precisions)
            if avg_precision < 0.8:
                # Reduce weight proportionally: low precision → lower weight
                scale = max(0.2, avg_precision / 0.8)
                calibrated_severity[sev] = round(DEFAULT_SEVERITY_WEIGHTS.get(sev, 0.3) * scale, 3)

        # ── Adjust concern boosts ─────────────────────────────────────
        calibrated_concern = dict(DEFAULT_CONCERN_BOOST)
        for con, precisions in concern_precision.items():
            avg_precision = sum(precisions) / len(precisions)
            if avg_precision < 0.8:
                scale = max(0.2, avg_precision / 0.8)
                calibrated_concern[con] = round(DEFAULT_CONCERN_BOOST.get(con, 0.0) * scale, 3)

        self._calibrated_severity = calibrated_severity
        self._calibrated_concern = calibrated_concern

        logger.info(
            "Trust calibration complete: %d samples, %d groups",
            len(outcomes),
            len(groups),
        )
        return True

    def get_adjusted_weights(self) -> dict[str, Any]:
        """Return calibrated weights or defaults if not yet calibrated.

        Returns:
            {
                "severity_weights": dict[str, float],
                "concern_boost": dict[str, float],
                "calibrated": bool,
                "sample_count": int,
            }
        """
        return {
            "severity_weights": self._calibrated_severity or dict(DEFAULT_SEVERITY_WEIGHTS),
            "concern_boost": self._calibrated_concern or dict(DEFAULT_CONCERN_BOOST),
            "calibrated": self._calibrated_severity is not None,
            "sample_count": self._last_sample_count,
        }

    async def get_calibration_stats(self) -> dict[str, Any]:
        """Return calibration statistics.

        Returns:
            {
                "precision_at_0.9": float,  # precision of findings with confidence >= 0.9
                "recall_at_0.6": float,     # recall of confirmed findings with confidence >= 0.6
                "sample_count": int,
                "false_positive_rate": float,
                "calibrated": bool,
            }
        """
        outcomes = await self._store.get_all_outcomes()

        if not outcomes:
            return {
                "precision_at_0.9": 0.0,
                "recall_at_0.6": 0.0,
                "sample_count": 0,
                "false_positive_rate": 0.0,
                "calibrated": self._calibrated_severity is not None,
            }

        # Precision at 0.9: of findings with confidence >= 0.9, how many are confirmed?
        high_conf = [
            o
            for o in outcomes
            if (o.get("confidence") or 0.0) >= 0.9
            and o.get("final_outcome") in ("confirmed", "false_positive")
        ]
        if high_conf:
            confirmed_high = sum(1 for o in high_conf if o["final_outcome"] == "confirmed")
            precision_high_conf = round(confirmed_high / len(high_conf), 3)
        else:
            precision_high_conf = 0.0

        # Recall at 0.6: of all confirmed findings, how many had confidence >= 0.6?
        all_confirmed = [o for o in outcomes if o.get("final_outcome") == "confirmed"]
        if all_confirmed:
            confirmed_high_conf = sum(
                1 for o in all_confirmed if (o.get("confidence") or 0.0) >= 0.6
            )
            recall_med_conf = round(confirmed_high_conf / len(all_confirmed), 3)
        else:
            recall_med_conf = 0.0

        # False positive rate
        total_relevant = [
            o for o in outcomes if o.get("final_outcome") in ("confirmed", "false_positive")
        ]
        fp_count = sum(1 for o in total_relevant if o["final_outcome"] == "false_positive")
        false_positive_rate = round(fp_count / len(total_relevant), 3) if total_relevant else 0.0

        return {
            "precision_at_0.9": precision_high_conf,
            "recall_at_0.6": recall_med_conf,
            "sample_count": len(outcomes),
            "false_positive_rate": false_positive_rate,
            "calibrated": self._calibrated_severity is not None,
        }
