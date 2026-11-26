"""
Posting gate for Verdity (non-negotiable constraint #7).

Every path that writes findings to a pull request must call
:func:`gate_findings` first and post **only** ``GateResult.postable``.

The gate is deterministic post-processing, in this order:

1. **Aggregate**: dedup and rank across specialists (``AggregatorAgent``).
2. **Route**: enforcement rules first, then composite confidence
   (``router.route``, constraint #5).
3. **Dispatch** each finding by its route:

   - ``BLOCK`` → posted, and the check run fails
   - ``AUTO_APPROVE`` → posted
   - ``MANUAL_REVIEW`` → Approval Queue, never posted
   - ``AUTO_DISMISS`` → audit only

4. **Audit**: one ``finding.routed`` record per finding (constraint #9).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from verdity.aggregator import AggregatorAgent
from verdity.router import RouteAction, RoutingDecision, route
from verdity.schemas import Finding, RepoRef, SpecialistResponse

if TYPE_CHECKING:  # TYPE_CHECKING
    from verdity.approval_queue import ApprovalQueue
    from verdity.audit_store import AuditStore
    from verdity.enforcement import EnforcementEngine
    from verdity.trust_calibration import TrustCalibrator

logger = logging.getLogger(__name__)


@dataclass
class GatedFinding:
    """A finding together with the routing decision that placed it."""

    finding: Finding
    decision: RoutingDecision


@dataclass
class GateResult:
    """Outcome of :func:`gate_findings`. Only ``postable`` may reach a PR."""

    postable: list[Finding]
    queued: list[GatedFinding]
    dismissed: list[GatedFinding]
    blocking: list[GatedFinding]
    skipped: int = field(default=0, compare=False)

    @property
    def conclusion(self) -> str:
        """GitHub check-run conclusion for this result."""
        if self.blocking:
            return "failure"
        if self.queued:
            return "neutral"
        return "success"

    def counts(self) -> dict[str, int]:
        return {
            "posted": len(self.postable),
            "queued": len(self.queued),
            "dismissed": len(self.dismissed),
            "blocking": len(self.blocking),
        }

    @property
    def summary_markdown(self) -> str:
        """Markdown that tells the PR what was posted and what was held back."""
        c = self.counts()
        lines = [
            "## Verdity verdict",
            "",
            f"- Posted: **{c['posted']}**",
            f"- Held for human review: **{c['queued']}**",
            f"- Dismissed as low confidence: **{c['dismissed']}**",
        ]
        if self.blocking:
            lines += ["", "### Blocking merge"]
            lines += [
                f"- `{g.finding.file}:{g.finding.line_start}` {g.finding.summary}"
                f" ({g.decision.reason})"
                for g in self.blocking
            ]
        return "\n".join(lines)


async def gate_findings(
    *,
    review_run_id: uuid.UUID,
    repo: RepoRef,
    responses: Iterable[Any],
    calibrator: TrustCalibrator | None = None,
    enforcement: EnforcementEngine | None = None,
    approval_queue: ApprovalQueue | None = None,
    audit: AuditStore | None = None,
) -> GateResult:
    """Aggregate, route and dispatch specialist findings.

    ``responses`` are specialist results. Only ``.findings`` is read, and items
    that aren't :class:`Finding` instances are skipped (and counted), never posted.
    """
    responses = list(responses)
    clean: list[SpecialistResponse] = []
    skipped = 0
    for resp in responses:
        findings = []
        for f in getattr(resp, "findings", None) or []:
            if isinstance(f, Finding):
                findings.append(f)
            else:
                skipped += 1
        clean.append(
            SpecialistResponse(
                review_run_id=review_run_id,
                specialist=str(getattr(resp, "specialist", "unknown")),
                status=str(getattr(resp, "status", "ok")),
                findings=findings,
            )
        )
    if skipped:
        logger.warning("Gate run %s: skipped %d non-Finding result(s)", review_run_id, skipped)

    # The router decides on composite confidence. Don't let the aggregator
    # drop findings on raw confidence first (threshold 0.0).
    aggregated = AggregatorAgent().aggregate(review_run_id, repo, clean, confidence_threshold=0.0)

    result = GateResult(postable=[], queued=[], dismissed=[], blocking=[], skipped=skipped)
    repo_id = f"{repo.owner}/{repo.name}"

    for ranked in aggregated.ranked_findings:
        finding = ranked.finding
        decision = await route(finding, calibrator=calibrator, enforcement_engine=enforcement)
        gated = GatedFinding(finding=finding, decision=decision)

        if decision.action == RouteAction.BLOCK:
            result.blocking.append(gated)
            result.postable.append(finding)
        elif decision.action == RouteAction.AUTO_APPROVE:
            result.postable.append(finding)
        elif decision.action == RouteAction.MANUAL_REVIEW:
            result.queued.append(gated)
            if approval_queue is not None:
                await approval_queue.enqueue(
                    run_id=review_run_id,
                    finding_id=finding.finding_id,
                    repo_id=repo_id,
                    concern=finding.concern.value,
                    severity=finding.severity.value,
                    file=finding.file,
                    line_start=finding.line_start,
                    summary=finding.summary,
                    explanation=finding.explanation,
                    confidence=decision.confidence,
                    route_action=decision.action.value,
                    route_reason=decision.reason,
                )
        else:
            result.dismissed.append(gated)

        if audit is not None:
            await audit.append(
                event_type="finding.routed",
                entity_type="finding",
                entity_id=str(finding.finding_id),
                payload={
                    "action": decision.action.value,
                    "confidence": decision.confidence,
                    "reason": decision.reason,
                    "repo": repo_id,
                    "file": finding.file,
                    "line_start": finding.line_start,
                },
                related_run_id=review_run_id,
            )

    if result.queued and approval_queue is None:
        logger.warning(
            "Gate run %s: %d finding(s) held for review but no approval queue is "
            "configured; they are reported in the result only",
            review_run_id,
            len(result.queued),
        )

    logger.info("Gate run %s: %s", review_run_id, result.counts())
    return result
