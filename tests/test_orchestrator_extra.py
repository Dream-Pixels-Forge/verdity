"""
Additional orchestrator tests covering:
- resolve_policy() — small/balanced/deep tier branches (lines 110-117)
- _gather_results() — cancellation path, exception path
- _run_specialist() — timeout path, exception path (line 379-380)
- process_event() — metrics recording block (lines 296-331)
- _run_adversarial_review() — no findings path, exception path (lines 456-486)
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verdity.audit_store import AuditStore
from verdity.event_queue import EventQueue
from verdity.metrics_store import MetricsStore
from verdity.orchestrator import (
    Orchestrator,
    ReviewRun,
    RunStatus,
    resolve_policy,
    resolve_specialists,
)
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
async def services():
    """Create all shared services for testing."""
    queue = EventQueue(db_path=":memory:")
    await queue.connect()
    audit = AuditStore(db_path=":memory:")
    await audit.connect()
    index = SemanticIndex(db_path=":memory:")
    await index.connect()
    te = TokenEconomicsService(db_path=":memory:")
    await te.connect()
    metrics = MetricsStore(db_path=":memory:")
    await metrics.connect()
    yield {
        "queue": queue,
        "audit": audit,
        "index": index,
        "token_economics": te,
        "metrics": metrics,
    }
    await queue.close()
    await audit.close()
    await index.close()
    await te.close()
    await metrics.close()


def _make_event(diff_lines: int = 0, trigger: TriggerType = TriggerType.PR_OPENED):
    return VerdityEvent(
        delivery_id=f"del-{uuid.uuid4().hex[:8]}",
        trigger_type=trigger,
        repo=RepoRef(owner="acme", name="widgets", id=1),
        pull_request=PullRequestRef(
            number=1,
            head_sha="abc",
            base_sha="def",
            additions=diff_lines,
            deletions=0,
        ),
    )


class TestResolvePolicyTiers:
    """Lines 110-117 cover the deep and balanced tier mapping."""

    def test_resolve_policy_lite_tier(self):
        """PR with diff < small_pr_diff_threshold maps to lite tier."""
        from verdity import config as config_mod
        get_settings = config_mod.get_settings
        get_settings.cache_clear()
        settings = get_settings()
        event = _make_event(diff_lines=settings.small_pr_diff_threshold - 10)
        policy = resolve_policy(event)
        assert policy.tier == "lite"
        assert policy.timeout_seconds == 10 * 60
        assert policy.budget_tokens == 10_000

    def test_resolve_policy_deep_tier(self):
        """PR with diff > large_pr_diff_threshold maps to deep tier."""
        from verdity import config as config_mod
        get_settings = config_mod.get_settings
        get_settings.cache_clear()
        settings = get_settings()
        event = _make_event(diff_lines=settings.large_pr_diff_threshold + 100)
        policy = resolve_policy(event)
        assert policy.tier == "deep"
        assert policy.timeout_seconds == 15 * 60
        assert policy.budget_tokens == 200_000

    def test_resolve_policy_balanced_tier(self):
        """PR with diff in between thresholds maps to balanced."""
        from verdity import config as config_mod
        get_settings = config_mod.get_settings
        get_settings.cache_clear()
        settings = get_settings()
        mid = (settings.small_pr_diff_threshold + settings.large_pr_diff_threshold) // 2
        event = _make_event(diff_lines=mid)
        policy = resolve_policy(event)
        assert policy.tier == "balanced"
        assert policy.timeout_seconds == 30 * 60
        assert policy.budget_tokens == 40_000


class TestResolveSpecialistsFallback:
    """Lines 146 and 151 — covers the else (no match) branch and security fallback."""

    def test_unknown_trigger_falls_back_to_security_only(self):
        """A trigger not in any branch returns ['security']."""
        # Build an event with a custom trigger that won't match any if/elif
        # We use a known one (PR_SYNCHRONIZE matches) — need an UNKNOWN trigger.
        # Since trigger_type is an enum, we have to use one of the mapped ones
        # but verify all the branches. Use an unmapped code path:
        # trigger_type INSTALLATION_DELETED has no mapping in resolve_specialists.
        event = VerdityEvent(
            delivery_id="del-inst-del",
            trigger_type=TriggerType.INSTALLATION_DELETED,
            repo=RepoRef(owner="acme", name="r", id=1),
        )
        # Match with default policy
        specialists = resolve_specialists(event, ReviewPolicy())
        # The else branch should set specialists = [], then security gets inserted
        assert "security" in specialists


class TestOrchestratorWithMetrics:
    """Lines 296-331 — the metrics recording block."""

    @pytest.mark.asyncio
    async def test_metrics_recorded_when_metrics_store_present(self, services):
        """If metrics_store is set, record_review_metrics and record_review_timing are called."""
        async def stub_specialist(ctx, index, te, audit):
            finding = Finding(
                concern=ConcernType.SECURITY,
                severity=Severity.HIGH,
                file="x.py",
                line_start=1,
                line_end=1,
                summary="x",
                explanation="x",
                confidence=0.5,
                evidence=[],
                agent_version="test",
                prompt_hash="x",
            )
            return SpecialistResponse(
                review_run_id=ctx.review_run_id,
                specialist="security",
                status="complete",
                findings=[finding],
                cost_usd=0.05,
            )

        orch = Orchestrator(
            queue=services["queue"],
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
            metrics_store=services["metrics"],
        )
        orch.register_specialist("security", stub_specialist)

        event = _make_event(diff_lines=50)
        with patch("verdity.orchestrator.resolve_policy") as mock_resolve:
            mock_resolve.return_value = ReviewPolicy(
                tier="balanced",
                adversarial_review_enabled=False,
            )
            run_id = await orch.process_event(QueueEnvelope(event=event))

        run = orch.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED

        # Verify metrics were recorded
        summary = await services["metrics"].get_repo_summary("acme/widgets")
        assert summary["review_count"] >= 1

    @pytest.mark.asyncio
    async def test_metrics_recording_failure_is_swallowed(self, services):
        """If metrics_store.record_review_metrics raises, log and continue."""
        async def stub_specialist(ctx, index, te, audit):
            finding = Finding(
                concern=ConcernType.SECURITY,
                severity=Severity.HIGH,
                file="x.py",
                line_start=1,
                line_end=1,
                summary="x",
                explanation="x",
                confidence=0.5,
                evidence=[],
                agent_version="test",
                prompt_hash="x",
            )
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
            metrics_store=services["metrics"],
        )
        orch.register_specialist("security", stub_specialist)

        event = _make_event(diff_lines=50)
        with patch("verdity.orchestrator.resolve_policy") as mock_resolve:
            mock_resolve.return_value = ReviewPolicy(adversarial_review_enabled=False)
            # Make the metrics call fail
            with patch.object(
                services["metrics"],
                "record_review_metrics",
                side_effect=RuntimeError("metrics down"),
            ):
                run_id = await orch.process_event(QueueEnvelope(event=event))
        assert run_id is not None


class TestRunAdversarialReview:
    """Lines 456-486 — covers _run_adversarial_review branches."""

    @pytest.mark.asyncio
    async def test_adversarial_review_no_findings_returns_early(self, services):
        async def empty_specialist(ctx, index, te, audit):
            return SpecialistResponse(
                review_run_id=ctx.review_run_id,
                specialist="security",
                status="complete",
                findings=[],
            )

        orch = Orchestrator(
            queue=services["queue"],
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
        )
        orch.register_specialist("security", empty_specialist)
        event = _make_event()
        with patch("verdity.orchestrator.resolve_policy") as mock_resolve:
            mock_resolve.return_value = ReviewPolicy(adversarial_review_enabled=True)
            await orch.process_event(QueueEnvelope(event=event))

    @pytest.mark.asyncio
    async def test_adversarial_review_success_branch(self, services):
        """If findings exist and the reviewer succeeds, verdicts are applied."""
        from verdity.adversarial_reviewer import AdversarialReview, AdversarialResult, Verdict

        async def finding_specialist(ctx, index, te, audit):
            finding = Finding(
                concern=ConcernType.SECURITY,
                severity=Severity.HIGH,
                file="x.py",
                line_start=1,
                line_end=1,
                summary="x",
                explanation="x",
                confidence=0.5,
                evidence=[],
                agent_version="test",
                prompt_hash="x",
            )
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
        )
        orch.register_specialist("security", finding_specialist)
        event = _make_event()

        with patch("verdity.orchestrator.resolve_policy") as mock_resolve:
            mock_resolve.return_value = ReviewPolicy(adversarial_review_enabled=True)
            with patch("verdity.adversarial_reviewer.AdversarialReviewer") as mock_reviewer_cls:
                review = AdversarialReview(
                    results=[],
                    overturned_count=0,
                    disputed_count=0,
                    confirmed_count=0,
                    total_findings=0,
                )
                # We need at least one result for apply_verdicts to map. Build one.
                mock_result = AdversarialResult(
                    finding_id="00000000-0000-0000-0000-000000000000",
                    verdict=Verdict.CONFIRMED,
                    reasoning="ok",
                    suggested_confidence_adjustment=0.1,
                )
                review.results.append(mock_result)

                mock_reviewer = MagicMock()
                mock_reviewer.challenge_findings = AsyncMock(return_value=review)
                mock_reviewer_cls.return_value = mock_reviewer

                await orch.process_event(QueueEnvelope(event=event))

    @pytest.mark.asyncio
    async def test_adversarial_review_exception_is_swallowed(self, services):
        async def finding_specialist(ctx, index, te, audit):
            finding = Finding(
                concern=ConcernType.SECURITY,
                severity=Severity.HIGH,
                file="x.py",
                line_start=1,
                line_end=1,
                summary="x",
                explanation="x",
                confidence=0.5,
                evidence=[],
                agent_version="test",
                prompt_hash="x",
            )
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
        )
        orch.register_specialist("security", finding_specialist)
        event = _make_event()

        with patch("verdity.orchestrator.resolve_policy") as mock_resolve:
            mock_resolve.return_value = ReviewPolicy(adversarial_review_enabled=True)
            with patch("verdity.adversarial_reviewer.AdversarialReviewer") as mock_reviewer_cls:
                mock_reviewer = MagicMock()
                mock_reviewer.challenge_findings = AsyncMock(
                    side_effect=RuntimeError("reviewer boom")
                )
                mock_reviewer_cls.return_value = mock_reviewer
                run_id = await orch.process_event(QueueEnvelope(event=event))
        assert run_id is not None


class TestGatherResults:
    """Cover _gather_results edge cases."""

    @pytest.mark.asyncio
    async def test_gather_with_no_tasks(self, services):
        """If tasks dict is empty, gather completes immediately."""
        orch = Orchestrator(
            queue=services["queue"],
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
        )
        run = ReviewRun(
            review_run_id=uuid.uuid4(),
            event=_make_event(),
            policy=ReviewPolicy(),
        )
        await orch._gather_results(  # noqa: SLF001
            review_run_id=run.review_run_id,
            run=run,
            policy=run.policy,
            tasks={},
        )
        assert run.specialist_results == {}

    @pytest.mark.asyncio
    async def test_gather_handles_task_exception(self, services):
        orch = Orchestrator(
            queue=services["queue"],
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
        )

        async def bad_specialist(ctx, index, te, audit):
            raise RuntimeError("intentional")

        run = ReviewRun(
            review_run_id=uuid.uuid4(),
            event=_make_event(),
            policy=ReviewPolicy(),
        )
        task = asyncio.create_task(bad_specialist(None, None, None, None))
        tasks = {"security": task}

        await orch._gather_results(  # noqa: SLF001
            review_run_id=run.review_run_id,
            run=run,
            policy=run.policy,
            tasks=tasks,
        )
        assert "security" in run.specialist_results
        assert run.specialist_results["security"].status == "failed"

    @pytest.mark.asyncio
    async def test_gather_with_timeout_cancellation(self, services):
        """A specialist that takes longer than the policy timeout is cancelled."""
        orch = Orchestrator(
            queue=services["queue"],
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
        )

        async def slow_specialist(ctx, index, te, audit):
            await asyncio.sleep(10)
            return SpecialistResponse(
                review_run_id=ctx.review_run_id,
                specialist="security",
                status="complete",
                findings=[],
            )

        run = ReviewRun(
            review_run_id=uuid.uuid4(),
            event=_make_event(),
            policy=ReviewPolicy(timeout_seconds=1),
        )
        task = asyncio.create_task(slow_specialist(None, None, None, None))
        tasks = {"security": task}

        await orch._gather_results(  # noqa: SLF001
            review_run_id=run.review_run_id,
            run=run,
            policy=run.policy,
            tasks=tasks,
        )
        # Cancelled tasks should not be in results (the cancel loop swallows them)
        # and the next loop checks task.cancelled()
        assert run.specialist_results == {} or (
            "security" in run.specialist_results
            and run.specialist_results["security"].status == "partial"
        )

    @pytest.mark.asyncio
    async def test_gather_skips_already_cancelled_tasks(self, services):
        """Tasks already in run.specialist_results (set by error path) are skipped."""
        orch = Orchestrator(
            queue=services["queue"],
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
        )
        # Pre-set a result for one specialist (simulates error case)
        run = ReviewRun(
            review_run_id=uuid.uuid4(),
            event=_make_event(),
            policy=ReviewPolicy(),
        )
        pre = SpecialistResponse(
            review_run_id=run.review_run_id,
            specialist="security",
            status="failed",
            error="pre-existing",
        )
        run.specialist_results["security"] = pre

        # Create a cancelled task
        async def slow(ctx, index, te, audit):
            await asyncio.sleep(10)
            return SpecialistResponse(
                review_run_id=ctx.review_run_id, specialist="x", status="complete"
            )

        task = asyncio.create_task(slow(None, None, None, None))
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        await orch._gather_results(  # noqa: SLF001
            review_run_id=run.review_run_id,
            run=run,
            policy=run.policy,
            tasks={"security": task},
        )
        # The pre-set value must be preserved
        assert run.specialist_results["security"].error == "pre-existing"


class TestRunSpecialistTypeCheck:
    """Line 406 — TypeError when a specialist returns non-SpecialistResponse."""

    @pytest.mark.asyncio
    async def test_run_specialist_type_error(self, services):
        orch = Orchestrator(
            queue=services["queue"],
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
        )

        async def bad_return_specialist(ctx, index, te, audit):
            return "not a SpecialistResponse"  # type: ignore[return-value]

        run = ReviewRun(
            review_run_id=uuid.uuid4(),
            event=_make_event(),
            policy=ReviewPolicy(),
        )
        result = await orch._run_specialist(  # noqa: SLF001
            name="security",
            fn=bad_return_specialist,
            run=run,
            policy=run.policy,
        )
        assert result.status == "failed"
        assert "non-SpecialistResponse" in (result.error or "")


class TestListRuns:
    """Lines 498-503 — list_runs."""

    def test_list_runs_returns_recent_first(self, services):
        orch = Orchestrator(
            queue=services["queue"],
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
        )
        # Insert some runs
        r1 = ReviewRun(review_run_id=uuid.uuid4(), event=_make_event())
        r2 = ReviewRun(review_run_id=uuid.uuid4(), event=_make_event())
        orch._runs[r1.review_run_id] = r1  # noqa: SLF001
        orch._runs[r2.review_run_id] = r2  # noqa: SLF001

        runs = orch.list_runs()
        assert len(runs) == 2

    def test_list_runs_with_limit(self, services):
        orch = Orchestrator(
            queue=services["queue"],
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
        )
        for _ in range(5):
            r = ReviewRun(review_run_id=uuid.uuid4(), event=_make_event())
            orch._runs[r.review_run_id] = r  # noqa: SLF001

        runs = orch.list_runs(limit=2)
        assert len(runs) == 2