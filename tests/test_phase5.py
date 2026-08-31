"""
Tests for Phase 5: Confidence Router + Approval Queue.
"""

from __future__ import annotations

import pytest
import uuid

from verdity.approval_queue import ApprovalQueueStore
from verdity.router import (
    RouteAction,
    compute_batch_routing,
    compute_confidence,
    route_finding,
)
from verdity.schemas import ConcernType, Finding, RankedFinding, Severity


def _make_finding(
    concern: ConcernType = ConcernType.SECURITY,
    severity: Severity = Severity.HIGH,
    confidence: float = 0.85,
    summary: str = "Test finding",
    file: str = "src/x.py",
    line: int = 10,
) -> Finding:
    return Finding(
        concern=concern,
        severity=severity,
        file=file,
        line_start=line,
        line_end=line,
        summary=summary,
        explanation="test",
        confidence=confidence,
        evidence=[],
        agent_version="test@0.0.0",
        prompt_hash="abc",
    )


class TestComputeConfidence:
    def test_high_severity_security_automatically_routed(self):
        f = _make_finding(confidence=0.9)
        score = compute_confidence(f)
        assert score > 0.7  # well into manual_review territory

    def test_low_severity_info_finding_has_low_score(self):
        f = _make_finding(severity=Severity.INFO, confidence=0.3)
        score = compute_confidence(f)
        assert score < 0.6  # auto_dismiss territory

    def test_critical_score_maxes_out(self):
        f = _make_finding(severity=Severity.CRITICAL, confidence=0.95)
        score = compute_confidence(f)
        assert score >= 0.85  # very high

    def test_score_clamped_to_unit_interval(self):
        f = _make_finding(severity=Severity.LOW, confidence=0.3)
        score = compute_confidence(f)
        assert 0.0 <= score <= 1.0


class TestRouteFinding:
    def test_auto_approve_threshold(self):
        f = _make_finding(severity=Severity.CRITICAL, confidence=0.99)
        score = compute_confidence(f)
        decision = route_finding(f, score)
        assert decision.action == RouteAction.AUTO_APPROVE
        assert "requires immediate action" in decision.reason

    def test_manual_review_range(self):
        f = _make_finding(concern=ConcernType.CODE_QUALITY, severity=Severity.HIGH, confidence=0.4)
        score = compute_confidence(f)
        decision = route_finding(f, score)
        assert decision.action == RouteAction.MANUAL_REVIEW
        assert "needs human review" in decision.reason

    def test_auto_dismiss_below_threshold(self):
        f = _make_finding(severity=Severity.INFO, confidence=0.3)
        score = compute_confidence(f)
        decision = route_finding(f, score)
        assert decision.action == RouteAction.AUTO_DISMISS

    def test_threshold_boundary(self):
        f = _make_finding(concern=ConcernType.CODE_QUALITY, severity=Severity.HIGH, confidence=0.3)
        score = compute_confidence(f)
        decision = route_finding(f, score)
        # HIGH severity (0.8 weight) × 0.3 + 0.8 = 0.86 → manual_review
        assert decision.action == RouteAction.MANUAL_REVIEW

    def test_auto_approve_critical_with_high_confidence(self):
        f = _make_finding(severity=Severity.CRITICAL, confidence=0.95)
        score = compute_confidence(f)
        decision = route_finding(f, score)
        # CRITICAL (1.0 weight) × 0.95 + 0.15 = 1.10, clamped to 1.0 → auto_approve
        assert decision.action == RouteAction.AUTO_APPROVE


class TestComputeBatchRouting:
    def test_batches_produce_decisions(self):
        findings = [
            _make_finding(severity=Severity.CRITICAL, confidence=0.95, summary="C1"),
            _make_finding(severity=Severity.INFO, confidence=0.3, summary="I1"),
            _make_finding(severity=Severity.HIGH, confidence=0.6, summary="H1"),
        ]
        results = compute_batch_routing(
            [RankedFinding(finding=f, rank_score=0.0) for f in findings]
        )
        assert len(results) == 3
        actions = {r.action for _, r in results}
        assert RouteAction.AUTO_APPROVE in actions
        assert RouteAction.AUTO_DISMISS in actions


@pytest.fixture
async def approval_store():
    store = ApprovalQueueStore(db_path=":memory:")
    await store.connect()
    yield store
    await store.close()


class TestApprovalQueueStore:
    @pytest.mark.asyncio
    async def test_enqueue_and_retrieve(self, approval_store):
        run_id = uuid.uuid4()
        finding_id = uuid.uuid4()
        await approval_store.enqueue(
            run_id=run_id,
            finding_id=finding_id,
            repo_id=42,
            concern="security",
            severity="high",
            file="src/x.py",
            line_start=10,
            summary="Secret detected",
            explanation="password found",
            confidence=0.85,
            route_action="manual_review",
            route_reason="medium confidence",
        )
        pending = await approval_store.get_pending(repo_id=42)
        assert len(pending) == 1
        assert pending[0]["summary"] == "Secret detected"
        assert pending[0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_resolve_marked_approved(self, approval_store):
        run_id = uuid.uuid4()
        finding_id = uuid.uuid4()
        await approval_store.enqueue(
            run_id=run_id,
            finding_id=finding_id,
            repo_id=1,
            concern="security",
            severity="critical",
            file="a.py",
            line_start=1,
            summary="S",
            explanation=None,
            confidence=0.95,
            route_action="auto_approve",
            route_reason="high conf",
        )
        items = await approval_store.get_pending(repo_id=1)
        qid = items[0]["id"]
        await approval_store.resolve(qid, reviewer_id="user1", action="approved")
        stats = await approval_store.stats(repo_id=1)
        assert stats.get("approved", 0) == 1
        assert stats.get("pending", 0) == 0

    @pytest.mark.asyncio
    async def test_stats_across_statuses(self, approval_store):
        for i in range(3):
            await approval_store.enqueue(
                run_id=uuid.uuid4(),
                finding_id=uuid.uuid4(),
                repo_id=1,
                concern="q",
                severity="low",
                file="f.py",
                line_start=i,
                summary=str(i),
                explanation=None,
                confidence=0.4,
                route_action="auto_dismiss",
                route_reason="low",
            )
        stats = await approval_store.stats(repo_id=1)
        assert stats.get("pending", 0) == 3
