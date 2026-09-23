"""
Approval gate tests (Issue #7).

Covers: orchestrator wiring (aggregator → compute_batch_routing → enqueue),
per-finding approval status lookup, and the fail-closed gate on the
GitHub-posting call site in CodingAgent.apply_fix_and_open_pr.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verdity.approval_queue import ApprovalQueueStore
from verdity.audit_store import AuditStore
from verdity.coding_agent import CodingAgent
from verdity.event_queue import EventQueue
from verdity.orchestrator import Orchestrator
from verdity.schemas import (
    ConcernType,
    Finding,
    PullRequestRef,
    QueueEnvelope,
    RepoRef,
    ReviewPolicy,
    Severity,
    SpecialistResponse,
    TriggerType,
    VerdityEvent,
)
from verdity.semantic_index import SemanticIndex
from verdity.token_economics import TokenEconomicsService


@pytest.fixture
async def approval_store():
    store = ApprovalQueueStore(db_path=":memory:")
    await store.connect()
    yield store
    await store.close()


@pytest.fixture
async def services():
    queue = EventQueue(db_path=":memory:")
    await queue.connect()
    audit = AuditStore(db_path=":memory:")
    await audit.connect()
    index = SemanticIndex(db_path=":memory:")
    await index.connect()
    te = TokenEconomicsService(db_path=":memory:")
    await te.connect()
    yield {
        "queue": queue,
        "audit": audit,
        "index": index,
        "token_economics": te,
    }
    await queue.close()
    await audit.close()
    await index.close()
    await te.close()


def _make_finding(**overrides) -> Finding:
    defaults = {
        "concern": ConcernType.SECURITY,
        "severity": Severity.HIGH,
        "file": "src/auth.py",
        "line_start": 10,
        "line_end": 10,
        "summary": "Hard-coded password detected",
        "explanation": "Use of hardcoded password",
        "confidence": 0.95,
        "evidence": [],
        "agent_version": "test@0.0.0",
        "prompt_hash": "sha256:test",
    }
    defaults.update(overrides)
    return Finding(**defaults)


def _make_event() -> VerdityEvent:
    return VerdityEvent(
        delivery_id="del-appr-001",
        trigger_type=TriggerType.PR_OPENED,
        repo=RepoRef(owner="acme", name="widgets", id=1),
        pull_request=PullRequestRef(number=7, head_sha="abc", base_sha="def"),
    )


class TestApprovalQueueStatusLookup:
    """ApprovalQueueStore.get_status — per-finding status lookup for the gate."""

    @pytest.mark.asyncio
    async def test_get_status_returns_status_after_enqueue_and_resolve(self, approval_store):
        finding_id = uuid.uuid4()
        await approval_store.enqueue(
            run_id=uuid.uuid4(),
            finding_id=finding_id,
            repo_id="acme/widgets",
            concern="security",
            severity="high",
            file="src/auth.py",
            line_start=10,
            summary="Hard-coded password detected",
            explanation=None,
            confidence=0.95,
            route_action="manual_review",
            route_reason=None,
        )
        assert await approval_store.get_status(finding_id) == "pending"

        pending = await approval_store.get_pending()
        await approval_store.resolve(pending[0]["id"], reviewer_id="rev-1", action="approved")
        assert await approval_store.get_status(finding_id) == "approved"

    @pytest.mark.asyncio
    async def test_get_status_unknown_finding_returns_none(self, approval_store):
        assert await approval_store.get_status(uuid.uuid4()) is None


class TestCodingAgentApprovalGate:
    """apply_fix_and_open_pr must fail closed unless status == 'approved'."""

    async def _enqueue_status(
        self, store: ApprovalQueueStore, finding: Finding, action: str
    ) -> None:
        await store.enqueue(
            run_id=uuid.uuid4(),
            finding_id=finding.finding_id,
            repo_id="acme/widgets",
            concern=finding.concern.value,
            severity=finding.severity.value,
            file=finding.file,
            line_start=finding.line_start,
            summary=finding.summary,
            explanation=finding.explanation,
            confidence=finding.confidence,
            route_action="manual_review",
            route_reason=None,
        )
        if action != "pending":
            pending = await store.get_pending()
            await store.resolve(pending[0]["id"], reviewer_id="rev-1", action=action)

    @pytest.mark.asyncio
    async def test_pending_finding_is_not_posted(self, approval_store):
        finding = _make_finding()
        await self._enqueue_status(approval_store, finding, "pending")

        agent = CodingAgent()
        with patch("verdity.github_client.GitHubClient") as mock_client_cls:
            result = await agent.apply_fix_and_open_pr(
                finding=finding,
                diff="+x",
                owner="acme",
                repo="widgets",
                approval_queue=approval_store,
            )

        assert result.success is False
        assert "approval" in (result.error or "").lower()
        mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_approved_finding_is_posted(self, approval_store):
        finding = _make_finding()
        await self._enqueue_status(approval_store, finding, "approved")

        agent = CodingAgent()
        with patch("verdity.github_client.GitHubClient") as mock_client_cls:
            mock_instance = MagicMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_instance.get_pr = AsyncMock(side_effect=RuntimeError("no pr"))
            mock_instance.post_pr_review = AsyncMock(return_value={"id": 99})
            mock_client_cls.return_value = mock_instance

            await agent.apply_fix_and_open_pr(
                finding=finding,
                diff="+x",
                owner="acme",
                repo="widgets",
                approval_queue=approval_store,
            )

        mock_client_cls.assert_called_once()
        mock_instance.post_pr_review.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_approval_queue_fails_closed(self):
        finding = _make_finding()

        agent = CodingAgent()
        with patch("verdity.github_client.GitHubClient") as mock_client_cls:
            result = await agent.apply_fix_and_open_pr(
                finding=finding,
                diff="+x",
                owner="acme",
                repo="widgets",
            )

        assert result.success is False
        assert "approval" in (result.error or "").lower()
        mock_client_cls.assert_not_called()

    @pytest.mark.asyncio
    async def test_unreachable_approval_queue_fails_closed(self):
        finding = _make_finding()
        unconnected = ApprovalQueueStore(db_path=":memory:")

        agent = CodingAgent()
        with patch("verdity.github_client.GitHubClient") as mock_client_cls:
            result = await agent.apply_fix_and_open_pr(
                finding=finding,
                diff="+x",
                owner="acme",
                repo="widgets",
                approval_queue=unconnected,
            )

        assert result.success is False
        assert "approval" in (result.error or "").lower()
        mock_client_cls.assert_not_called()


class TestOrchestratorApprovalWiring:
    """Aggregator output → compute_batch_routing → ApprovalQueueStore.enqueue."""

    @pytest.mark.asyncio
    async def test_auto_approve_routed_finding_enqueued(self, services, approval_store):
        finding = _make_finding()

        async def stub_specialist(ctx, index, te, audit):
            return SpecialistResponse(
                review_run_id=ctx.review_run_id,
                specialist="security",
                status="complete",
                findings=[finding],
            )

        orch = Orchestrator(
            queue=services["queue"],
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
            approval_queue=approval_store,
        )
        orch.register_specialist("security", stub_specialist)

        with patch("verdity.orchestrator.resolve_policy") as mock_resolve:
            mock_resolve.return_value = ReviewPolicy(adversarial_review_enabled=False)
            run_id = await orch.process_event(QueueEnvelope(event=_make_event()))

        assert orch.get_run(run_id) is not None
        pending = await approval_store.get_pending(repo_id="acme/widgets")
        assert len(pending) == 1
        assert pending[0]["finding_id"] == str(finding.finding_id)
        assert pending[0]["status"] == "pending"
        assert pending[0]["route_action"] == "auto_approve"
        assert pending[0]["summary"] == finding.summary

    @pytest.mark.asyncio
    async def test_manual_review_routed_finding_enqueued(self, services, approval_store):
        finding = _make_finding(
            concern=ConcernType.CODE_QUALITY,
            severity=Severity.INFO,
            confidence=0.75,
            summary="Bare except clause",
        )

        async def stub_specialist(ctx, index, te, audit):
            return SpecialistResponse(
                review_run_id=ctx.review_run_id,
                specialist="security",
                status="complete",
                findings=[finding],
            )

        orch = Orchestrator(
            queue=services["queue"],
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
            approval_queue=approval_store,
        )
        orch.register_specialist("security", stub_specialist)

        with patch("verdity.orchestrator.resolve_policy") as mock_resolve:
            mock_resolve.return_value = ReviewPolicy(adversarial_review_enabled=False)
            run_id = await orch.process_event(QueueEnvelope(event=_make_event()))

        assert orch.get_run(run_id) is not None
        pending = await approval_store.get_pending(repo_id="acme/widgets")
        assert len(pending) == 1
        assert pending[0]["finding_id"] == str(finding.finding_id)
        assert pending[0]["route_action"] == "manual_review"

    @pytest.mark.asyncio
    async def test_orchestrator_without_approval_queue_still_completes(self, services):
        async def stub_specialist(ctx, index, te, audit):
            return SpecialistResponse(
                review_run_id=ctx.review_run_id,
                specialist="security",
                status="complete",
                findings=[_make_finding()],
            )

        orch = Orchestrator(
            queue=services["queue"],
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
        )
        orch.register_specialist("security", stub_specialist)

        with patch("verdity.orchestrator.resolve_policy") as mock_resolve:
            mock_resolve.return_value = ReviewPolicy(adversarial_review_enabled=False)
            run_id = await orch.process_event(QueueEnvelope(event=_make_event()))

        run = orch.get_run(run_id)
        assert run is not None
        assert run.status.value in ("completed", "partial")
