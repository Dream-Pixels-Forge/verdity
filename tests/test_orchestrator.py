"""
Tests for the Orchestrator (Phase 3).
"""

from __future__ import annotations

import pytest
import uuid

from verdity.audit_store import AuditStore
from verdity.event_queue import EventQueue
from verdity.orchestrator import Orchestrator, ReviewRun, RunStatus, resolve_policy, resolve_specialists
from verdity.schemas import (
    ConcernType,
    Finding,
    QueueEnvelope,
    RepoRef,
    ReviewPolicy,
    SpecialistResponse,
    TriggerType,
    VerdityEvent,
)
from verdity.schemas._models import SpecialistContext
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


@pytest.fixture
def sample_pr_event():
    return VerdityEvent(
        delivery_id="del-sample-001",
        trigger_type=TriggerType.PR_OPENED,
        repo=RepoRef(owner="acme", name="widgets", id=1),
    )


class TestResolvePolicy:
    def test_pr_opened_standard(self, sample_pr_event):
        policy = resolve_policy(sample_pr_event)
        assert policy.depth == "standard"
        assert policy.timeout_seconds == 120  # default from resolve_policy
        assert policy.budget_tokens == 40000

    def test_push_event(self):
        event = VerdityEvent(
            delivery_id="del-push",
            trigger_type=TriggerType.PUSH,
            repo=RepoRef(owner="acme", name="r", id=1),
        )
        policy = resolve_policy(event)
        assert policy.timeout_seconds == 30  # shorter for index-only
        assert policy.budget_tokens == 5000

    def test_installation_event(self):
        event = VerdityEvent(
            delivery_id="del-inst",
            trigger_type=TriggerType.INSTALLATION_CREATED,
            repo=RepoRef(owner="acme", name="r", id=1),
       )
        policy = resolve_policy(event)
        assert policy.timeout_seconds == 10
        assert policy.budget_tokens == 1000


class TestResolveSpecialists:
    def test_pr_opened_all_four(self, sample_pr_event):
        policy = resolve_policy(sample_pr_event)
        specialists = resolve_specialists(sample_pr_event, policy)
        assert "security" in specialists
        assert "code_quality" in specialists
        assert "testing" in specialists
        assert "documentation" in specialists

    def test_pr_synchronize_all(self):
        event = VerdityEvent(
            delivery_id="del-sync",
            trigger_type=TriggerType.PR_SYNCHRONIZE,
            repo=RepoRef(owner="acme", name="r", id=1),
        )
        policy = resolve_policy(event)
        specialists = resolve_specialists(event, policy)
        assert "security" in specialists

    def test_review_comment_single(self):
        event = VerdityEvent(
            delivery_id="del-comment",
            trigger_type=TriggerType.REVIEW_COMMENT_CREATED,
            repo=RepoRef(owner="acme", name="r", id=1),
        )
        policy = resolve_policy(event)
        specialists = resolve_specialists(event, policy)
        assert specialists == ["security"]  # Phase 3: single specialist


class TestOrchestratorRun:
    @pytest.mark.asyncio
    async def test_run_with_registered_security_agent(self, services, sample_pr_event):
        """Full integration: event → orchestrator → security agent → audit."""
        from verdity.agents.security import SecurityAgent

        orch = Orchestrator(
            queue=services["queue"],
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
        )

        agent = SecurityAgent()
        orch.register_specialist("security", agent.run)

        orch.register_specialist("security", agent.run)

        # Publish event to queue
        envelope = QueueEnvelope(event=sample_pr_event)
        await services["queue"].publish(envelope)

        # Process
        run_id = await orch.process_event(envelope)
        assert run_id is not None

        # Verify run completed
        run = orch.get_run(run_id)
        assert run is not None
        assert run.status in (RunStatus.COMPLETED, RunStatus.PARTIAL)
        assert "security" in run.specialist_results

        # Verify audit log has entries
        audit_records = await services["audit"].query_by_run(run_id)
        assert len(audit_records) >= 2  # run_started + run_completed + findings

    @pytest.mark.asyncio
    async def test_unregistered_specialist_does_not_block(self, services, sample_pr_event):
        """Constraint #3: one missing specialist must not block others."""
        orch = Orchestrator(
            queue=services["queue"],
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
        )
        # No specialists registered — should still complete gracefully

        envelope = QueueEnvelope(event=sample_pr_event)
        run_id = await orch.process_event(envelope)
        run = orch.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.COMPLETED
        # security should be in results as "failed" (not registered)
        assert "security" in run.specialist_results
        assert run.specialist_results["security"].status == "failed"

    @pytest.mark.asyncio
    async def test_specialist_timeout_handled(self, services, sample_pr_event):
        """Constraint #3: specialist timeout must not block the run."""
        async def slow_specialist(ctx, index, te, audit):
            import asyncio
            await asyncio.sleep(10)  # way longer than timeout
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
        orch.register_specialist("security", slow_specialist)

        # The policy is resolved inside process_event, so we can't easily override it.
        # Instead we test the _run_specialist method directly.
        run = ReviewRun(
            review_run_id=uuid.uuid4(),
            event=sample_pr_event,
            policy=ReviewPolicy(timeout_seconds=1),
        )
        result = await orch._run_specialist("security", slow_specialist, run, run.policy)
        assert result.status == "partial"
        assert "Timed out" in result.error

    @pytest.mark.asyncio
    async def test_specialist_failure_handled(self, services, sample_pr_event):
        """A failing specialist must not crash the run."""
        async def failing_specialist(ctx, index, te, audit):
            raise RuntimeError("intentional failure")

        orch = Orchestrator(
            queue=services["queue"],
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
        )
        orch.register_specialist("security", failing_specialist)

        envelope = QueueEnvelope(event=sample_pr_event)
        run_id = await orch.process_event(envelope)
        run = orch.get_run(run_id)
        assert run is not None
        assert run.specialist_results["security"].status == "failed"
        assert "intentional failure" in run.specialist_results["security"].error

    @pytest.mark.asyncio
    async def test_security_agent_produces_schema_valid_findings(self, services):
        """Security agent must produce findings that pass Pydantic validation."""
        from verdity.agents.security import SecurityAgent

        agent = SecurityAgent()
        run_id = uuid.uuid4()
        policy = ReviewPolicy()

        diff_files = [
            {
                "path": "src/auth.py",
                "content": "password = 'supersecret123'\ndef login(u, p):\n    return verify(u, p)",
                "additions": "password = 'supersecret123'\n",
                "deletions": "",
            },
            {
                "path": "src/safe.py",
                "content": "def hello():\n    return 'world'",
                "additions": "",
                "deletions": "",
            },
        ]

        result = await agent.run(
            ctx=SpecialistContext(
                review_run_id=run_id,
                repo_owner="acme", repo_name="widgets",
                base_sha="abc", head_sha="def",
                diff_files=diff_files,
                policy=policy,
            ),
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
        )

        assert isinstance(result, SpecialistResponse)
        assert result.status == "complete"
        assert isinstance(result.findings, list)
        # At least one finding for the secret
        assert len(result.findings) >= 1
        for f in result.findings:
            assert isinstance(f, Finding)
            assert f.concern == ConcernType.SECURITY
            assert 0.0 <= f.confidence <= 1.0
            assert len(f.evidence) > 0
            assert f.agent_version == agent.AGENT_VERSION

    @pytest.mark.asyncio
    async def test_security_agent_audit_logging(self, services):
        """Every finding must be logged to the Audit Store (constraint #9)."""
        from verdity.agents.security import SecurityAgent

        agent = SecurityAgent()
        run_id = uuid.uuid4()

        diff_files = [
            {
                "path": "src/config.py",
                "content": "API_KEY = 'sk-abc123'",
                "additions": "API_KEY = 'sk-abc123'\n",
                "deletions": "",
            },
        ]

        result = await agent.run(
            ctx=SpecialistContext(
                review_run_id=run_id,
                repo_owner="acme", repo_name="widgets",
                base_sha="abc", head_sha="def",
                diff_files=diff_files,
                policy=ReviewPolicy(),
            ),
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
        )

        # Check audit log - agent logs findings directly (orchestrator logs run events)
        records = await services["audit"].query_by_run(run_id)
        finding_records = [r for r in records if r["event_type"] == "finding.created"]
        assert len(finding_records) == len(result.findings)

    @pytest.mark.asyncio
    async def test_security_agent_token_metering(self, services):
        """Every agent call must be metered (constraint #8)."""
        from verdity.agents.security import SecurityAgent

        agent = SecurityAgent()
        run_id = uuid.uuid4()

        diff_files = [
            {"path": "x.py", "content": "eval(user_input)", "additions": "eval(user_input)", "deletions": ""},
        ]

        await agent.run(
            ctx=SpecialistContext(
                review_run_id=run_id,
                repo_owner="acme", repo_name="widgets",
                base_sha="abc", head_sha="def",
                diff_files=diff_files,
                policy=ReviewPolicy(),
            ),
            semantic_index=services["index"],
            token_economics=services["token_economics"],
            audit_store=services["audit"],
        )

        # Check token economics
        stats = await services["token_economics"].get_spend(repo_owner="acme", repo_name="widgets")
        assert stats["total_calls"] >= 1
        assert stats["tokens_in"] > 0


class TestConfidenceComputation:
    """Per Orchestration doc §4: confidence is deterministic, not LLM self-report."""

    def test_secret_in_comment_has_low_confidence(self):
        from verdity.agents.security import SecurityAgent
        agent = SecurityAgent()
        # Pattern in a comment should reduce confidence
        conf = agent._compute_secret_confidence("AWS_ACCESS_KEY", "# aws_access_key = 'old'")
        assert conf < 0.85  # comment = reduced from base 0.85

    def test_private_key_pattern_has_high_confidence(self):
        from verdity.agents.security import SecurityAgent
        agent = SecurityAgent()
        conf = agent._compute_secret_confidence("PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----")
        assert conf >= 0.9  # definitive marker

    def test_secret_in_new_code_boosted(self):
        from verdity.agents.security import SecurityAgent
        agent = SecurityAgent()
        conf = agent._compute_secret_confidence("AWS_ACCESS_KEY", "+ aws_access_key = 'new_value'")
        assert conf >= 0.85  # new code = higher confidence
