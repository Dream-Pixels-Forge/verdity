"""
Confidence scoring and routing logic for Verdity.

Deterministic multi-signal confidence computation per constraint #5.
Routes findings to review queue based on composite score thresholds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from verdity.enforcement import EnforcementEngine
from verdity.metrics_store import MetricsStore
from verdity.schemas import (
    ConcernType,
    Finding,
    RankedFinding,
    Severity,
)

if TYPE_CHECKING:
    from verdity.trust_calibration import TrustCalibrator

logger = logging.getLogger(__name__)


class RouteAction(StrEnum):
    AUTO_APPROVE = "auto_approve"
    MANUAL_REVIEW = "manual_review"
    AUTO_DISMISS = "auto_dismiss"


@dataclass
class RoutingDecision:
    action: RouteAction
    confidence: float
    reason: str


# ── Signal weights ────────────────────────────────────────────────────

DEFAULT_SEVERITY_WEIGHTS = {
    Severity.CRITICAL: 1.0,
    Severity.HIGH: 0.8,
    Severity.MEDIUM: 0.5,
    Severity.LOW: 0.3,
    Severity.INFO: 0.1,
}

# Concern-type boost — security findings get a small bump
DEFAULT_CONCERN_BOOST = {
    ConcernType.SECURITY: 0.15,
    ConcernType.CODE_QUALITY: 0.0,
    ConcernType.TESTING: 0.05,
    ConcernType.DOCUMENTATION: 0.0,
}

# Thresholds
AUTO_APPROVE_THRESHOLD = 0.9
MANUAL_REVIEW_THRESHOLD = 0.6
# Below MANUAL_REVIEW_THRESHOLD → auto_dismiss


def compute_confidence(
    finding: Finding,
    context: dict[str, Any] | None = None,
    *,
    severity_weights: dict[str, float] | None = None,
    concern_boost: dict[str, float] | None = None,
) -> float:
    """
    Compute deterministic multi-signal confidence score.

    Per constraint #5: confidence is never LLM self-reported.
    Score = base_confidence * (1 - sev_weight) + sev_weight + concern_boost, clamped to [0, 1].

    This treats severity as a floor/baseline rather than a multiplier:
    - CRITICAL findings get a floor of 1.0 (always matter)
    - LOW findings get a floor of 0.3
    - The base confidence fills the gap between floor and 1.0

    Args:
        finding: the finding to score
        context: optional context dict (unused, reserved for future signals)
        severity_weights: optional calibrated severity weights (overrides defaults)
        concern_boost: optional calibrated concern boosts (overrides defaults)
    """
    base = finding.confidence
    sev_w = severity_weights or DEFAULT_SEVERITY_WEIGHTS
    con_b = concern_boost or DEFAULT_CONCERN_BOOST

    sev_weight = sev_w.get(
        finding.severity.value if hasattr(finding.severity, "value") else str(finding.severity), 0.3
    )
    boost = con_b.get(
        finding.concern.value if hasattr(finding.concern, "value") else str(finding.concern), 0.0
    )

    # Blend: severity sets the floor, base fills remaining range
    score = base * (1.0 - sev_weight) + sev_weight + boost
    return round(max(0.0, min(1.0, score)), 3)


def route_finding(finding: Finding, confidence: float) -> RoutingDecision:
    """
    Route a finding based on its composite confidence score.

    Rules:
      ≥ 0.9 → AUTO_APPROVE (action required)
      ≥ 0.6 → MANUAL_REVIEW (needs human judgment)
      <  0.6 → AUTO_DISMISS (too low confidence to act on)
    """
    if confidence >= AUTO_APPROVE_THRESHOLD:
        return RoutingDecision(
            action=RouteAction.AUTO_APPROVE,
            confidence=confidence,
            reason=f"High confidence ({confidence:.2f}) — requires immediate action",
        )
    if confidence >= MANUAL_REVIEW_THRESHOLD:
        return RoutingDecision(
            action=RouteAction.MANUAL_REVIEW,
            confidence=confidence,
            reason=f"Medium confidence ({confidence:.2f}) — needs human review",
        )
    return RoutingDecision(
        action=RouteAction.AUTO_DISMISS,
        confidence=confidence,
        reason=f"Low confidence ({confidence:.2f}) — dismissed automatically",
    )


async def route(
    finding: Finding,
    calibrator: TrustCalibrator | None = None,
    context: dict[str, Any] | None = None,
    enforcement_engine: EnforcementEngine | None = None,
) -> RoutingDecision:
    """
    Compute confidence (with optional calibration) and route a finding.

    This is the main entry point for routing a single finding. It optionally
    uses TrustCalibrator to get adjusted weights, then computes confidence
    and applies routing thresholds.

    Args:
        finding: the finding to route
        calibrator: optional TrustCalibrator for calibrated weights
        context: optional context dict for additional signals
        enforcement_engine: optional EnforcementEngine for blocking rules

    Returns:
        RoutingDecision with action, confidence, and reason
    """
    # First, evaluate enforcement rules
    if enforcement_engine is not None:
        enforcement_decision = await enforcement_engine.evaluate(finding)
        if enforcement_decision.blocked:
            # Map enforcement action to RouteAction
            action_map = {
                "BLOCK": RouteAction.MANUAL_REVIEW,
                "REQUIRE_APPROVAL": RouteAction.MANUAL_REVIEW,
                "ESCALATE": RouteAction.MANUAL_REVIEW,
            }
            return RoutingDecision(
                action=action_map.get(
                    enforcement_decision.action.upper(), RouteAction.MANUAL_REVIEW
                ),
                confidence=finding.confidence,
                reason=f"Enforcement: {enforcement_decision.message}",
            )

    if calibrator is not None:
        severity_weights, concern_boost = await calibrator.get_adjusted_weights()
    else:
        severity_weights = None
        concern_boost = None

    confidence = compute_confidence(
        finding,
        context,
        severity_weights=severity_weights,
        concern_boost=concern_boost,
    )

    # Store calibration info on finding for traceability
    if calibrator is not None:
        stats = await calibrator.get_calibration_stats()
        finding.confidence_signals = {
            "base_confidence": finding.confidence,
            "severity_weight": severity_weights.get(
                finding.severity.value
                if hasattr(finding.severity, "value")
                else str(finding.severity),
                0.3,
            )
            if severity_weights
            else DEFAULT_SEVERITY_WEIGHTS.get(
                finding.severity.value
                if hasattr(finding.severity, "value")
                else str(finding.severity),
                0.3,
            ),
            "concern_boost": concern_boost.get(
                finding.concern.value
                if hasattr(finding.concern, "value")
                else str(finding.concern),
                0.0,
            )
            if concern_boost
            else DEFAULT_CONCERN_BOOST.get(
                finding.concern.value
                if hasattr(finding.concern, "value")
                else str(finding.concern),
                0.0,
            ),
        }
        finding.calibration_version = stats.get("version", 0)

    return route_finding(finding, confidence)


def compute_batch_routing(
    ranked_findings: list[RankedFinding],
    context: dict[str, Any] | None = None,
    *,
    severity_weights: dict[str, float] | None = None,
    concern_boost: dict[str, float] | None = None,
) -> list[tuple[Finding, RoutingDecision]]:
    """Compute routing decisions for a batch of ranked findings.

    Args:
        ranked_findings: findings to route
        context: optional context dict
        severity_weights: optional calibrated severity weights
        concern_boost: optional calibrated concern boosts
    """
    decisions = []
    for rf in ranked_findings:
        conf = compute_confidence(
            rf.finding,
            context,
            severity_weights=severity_weights,
            concern_boost=concern_boost,
        )
        decision = route_finding(rf.finding, conf)
        decisions.append((rf.finding, decision))
    return decisions


async def record_routing_outcomes(
    metrics_store: MetricsStore,
    decisions: list[tuple[Finding, RoutingDecision]],
    *,
    repo_id: str,
    pr_number: int | None = None,
) -> None:
    """Record routing outcomes to the metrics store.

    Auto-approved findings → 'auto_fixed' outcome
    Auto-dismissed findings → 'false_positive' outcome
    Manual review findings → no outcome recorded (awaiting human decision)
    """
    for finding, decision in decisions:
        outcome_map = {
            RouteAction.AUTO_APPROVE: "auto_fixed",
            RouteAction.AUTO_DISMISS: "false_positive",
        }
        outcome = outcome_map.get(decision.action)
        if outcome is None:
            continue  # manual_review — no automatic outcome
        await metrics_store.record_finding_outcome(
            finding_id=str(finding.finding_id),
            repo_id=repo_id,
            pr_number=pr_number,
            final_outcome=outcome,
            confidence=decision.confidence,
            severity=finding.severity.value,
            concern=finding.concern.value,
        )
