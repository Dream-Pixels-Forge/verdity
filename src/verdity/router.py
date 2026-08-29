"""
Confidence scoring and routing logic for Verdity.

Deterministic multi-signal confidence computation per constraint #5.
Routes findings to review queue based on composite score thresholds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from verdity.schemas import (
    ConcernType,
    Finding,
    RankedFinding,
    Severity,
)

logger = logging.getLogger(__name__)


class RouteAction(str, Enum):
    AUTO_APPROVE = "auto_approve"
    MANUAL_REVIEW = "manual_review"
    AUTO_DISMISS = "auto_dismiss"


@dataclass
class RoutingDecision:
    action: RouteAction
    confidence: float
    reason: str


# ── Signal weights ────────────────────────────────────────────────────

_SEVERITY_WEIGHTS = {
    Severity.CRITICAL: 1.0,
    Severity.HIGH: 0.8,
    Severity.MEDIUM: 0.5,
    Severity.LOW: 0.3,
    Severity.INFO: 0.1,
}

# Concern-type boost — security findings get a small bump
_CONCERN_BOOST = {
    ConcernType.SECURITY: 0.15,
    ConcernType.CODE_QUALITY: 0.0,
    ConcernType.TESTING: 0.05,
    ConcernType.DOCUMENTATION: 0.0,
}

# Thresholds
AUTO_APPROVE_THRESHOLD = 0.9
MANUAL_REVIEW_THRESHOLD = 0.6
# Below MANUAL_REVIEW_THRESHOLD → auto_dismiss


def compute_confidence(finding: Finding, context: dict[str, Any] | None = None) -> float:
    """
    Compute deterministic multi-signal confidence score.

    Per constraint #5: confidence is never LLM self-reported.
    Score = base_confidence × (1 - sev_weight) + sev_weight + concern_boost, clamped to [0, 1].

    This treats severity as a floor/baseline rather than a multiplier:
    - CRITICAL findings get a floor of 1.0 (always matter)
    - LOW findings get a floor of 0.3
    - The base confidence fills the gap between floor and 1.0
    """
    base = finding.confidence
    sev_weight = _SEVERITY_WEIGHTS.get(finding.severity, 0.3)
    concern_boost = _CONCERN_BOOST.get(finding.concern, 0.0)

    # Blend: severity sets the floor, base fills remaining range
    score = base * (1.0 - sev_weight) + sev_weight + concern_boost
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
    elif confidence >= MANUAL_REVIEW_THRESHOLD:
        return RoutingDecision(
            action=RouteAction.MANUAL_REVIEW,
            confidence=confidence,
            reason=f"Medium confidence ({confidence:.2f}) — needs human review",
        )
    else:
        return RoutingDecision(
            action=RouteAction.AUTO_DISMISS,
            confidence=confidence,
            reason=f"Low confidence ({confidence:.2f}) — dismissed automatically",
        )


def compute_batch_routing(
    ranked_findings: list[RankedFinding],
    context: dict[str, Any] | None = None,
) -> list[tuple[Finding, RoutingDecision]]:
    """Compute routing decisions for a batch of ranked findings."""
    decisions = []
    for rf in ranked_findings:
        conf = compute_confidence(rf.finding, context)
        decision = route_finding(rf.finding, conf)
        decisions.append((rf.finding, decision))
    return decisions
