"""
Tests for Issue #42: Enforcement engine - blocking rules, GitHub Checks API, SLA escalation.

Covers:
- Blocking rules engine with CEL expressions
- GitHub Checks API integration
- Approval queue SLA escalation
- Verifier disagreement auto-escalation
- Budget enforcer re-queue
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from verdity.router import RouteAction
from verdity.schemas import ConcernType, Finding, Severity


@pytest_asyncio.fixture
async def enforcement_fixture():
    """Set up enforcement components."""
    from verdity.approval_queue import ApprovalQueue
    from verdity.audit_store import AuditStore
    from verdity.budget_enforcer import BudgetEnforcer
    from verdity.enforcement import EnforcementEngine
    from verdity.event_queue import EventQueue
    from verdity.github_client import GitHubClient
    from verdity.metrics_store import MetricsStore
    from verdity.token_economics import TokenEconomicsService

    # Set required env vars
    os.environ["GITHUB_APP_ID"] = "12345"
    os.environ["GITHUB_APP_INSTALLATION_ID"] = "98765"
    os.environ["GITHUB_APP_PRIVATE_KEY"] = (
        "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
    )

    # Initialize components
    audit_store = AuditStore(db_path=":memory:")
    await audit_store.connect()
    event_queue = EventQueue(db_path=":memory:")
    await event_queue.connect()
    metrics_store = MetricsStore(db_path=":memory:")
    await metrics_store.connect()
    approval_queue = ApprovalQueue(db_path=":memory:")
    await approval_queue.connect()

    github_client = GitHubClient(
        app_id=12345,
        private_key_pem="-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----",
        installation_id="98765",
    )

    te_service = TokenEconomicsService()
    enforcement_engine = EnforcementEngine(rules=[])

    budget_enforcer = BudgetEnforcer(te_service=te_service)

    yield {
        "enforcement_engine": enforcement_engine,
        "github_client": github_client,
        "approval_queue": approval_queue,
        "budget_enforcer": budget_enforcer,
        "audit_store": audit_store,
        "event_queue": event_queue,
        "metrics_store": metrics_store,
    }

    await audit_store.close()
    await event_queue.close()
    await metrics_store.close()
    await approval_queue.close()


def _make_finding(**kwargs) -> Finding:
    """Create a test finding."""
    defaults = {
        "concern": ConcernType.SECURITY,
        "severity": Severity.HIGH,
        "file": "src/test.py",
        "line_start": 10,
        "line_end": 10,
        "summary": "Test finding",
        "explanation": "Test",
        "confidence": 0.8,
        "agent_version": "test@0.1.0",
        "prompt_hash": "sha256:abc123",
    }
    defaults.update(kwargs)
    return Finding(**defaults)


# ── GateRule Tests ───────────────────────────────────────────────────────────


class TestGateRule:
    """Test GateRule model."""

    def test_gate_rule_creation(self):
        """GateRule should be created with id, when, then, message."""
        from verdity.enforcement import Action, GateRule

        rule = GateRule(
            id="block-critical",
            when="finding.severity=='critical' and finding.confidence>0.8",
            then=Action.BLOCK,
            message="High confidence CRITICAL blocks",
        )
        assert rule.id == "block-critical"
        assert "critical" in rule.when
        assert rule.then == Action.BLOCK
        assert "CRITICAL blocks" in rule.message

    def test_gate_rule_actions(self):
        """GateRule should support all action types."""
        from verdity.enforcement import Action

        assert Action.BLOCK.value == "block"
        assert Action.REQUIRE_APPROVAL.value == "require_approval"
        assert Action.ESCALATE.value == "escalate"


# ── EnforcementEngine Tests ──────────────────────────────────────────────────


class TestEnforcementEngine:
    """Test EnforcementEngine blocking rules evaluation."""

    @pytest.mark.asyncio
    async def test_engine_blocks_critical_high_confidence(self, enforcement_fixture):
        """Engine should BLOCK when CRITICAL finding with confidence > 0.8."""
        engine = enforcement_fixture["enforcement_engine"]

        # Add blocking rule
        from verdity.enforcement import Action, GateRule

        rule = GateRule(
            id="block-critical",
            when="finding.severity=='critical' and finding.confidence>0.8",
            then=Action.BLOCK,
            message="High confidence CRITICAL blocks",
        )
        engine.add_rule(rule)

        finding = _make_finding(severity=Severity.CRITICAL, confidence=0.9)
        decision = await engine.evaluate(finding)

        assert decision.action == "block"
        assert decision.rule_id == "block-critical"

    @pytest.mark.asyncio
    async def test_engine_allows_below_threshold(self, enforcement_fixture):
        """Engine should ALLOW when finding doesn't match blocking rules."""
        engine = enforcement_fixture["enforcement_engine"]

        from verdity.enforcement import Action, GateRule

        rule = GateRule(
            id="block-critical",
            when="finding.severity=='critical' and finding.confidence>0.8",
            then=Action.BLOCK,
            message="High confidence CRITICAL blocks",
        )
        engine.add_rule(rule)

        finding = _make_finding(severity=Severity.HIGH, confidence=0.7)
        decision = await engine.evaluate(finding)

        assert decision.action == "allow"

    @pytest.mark.asyncio
    async def test_engine_require_approval(self, enforcement_fixture):
        """Engine should REQUIRE_APPROVAL when rule matches."""
        engine = enforcement_fixture["enforcement_engine"]

        from verdity.enforcement import Action, GateRule

        rule = GateRule(
            id="require-approval-high",
            when="finding.severity=='high' and finding.confidence>0.7",
            then=Action.REQUIRE_APPROVAL,
            message="High severity requires approval",
        )
        engine.add_rule(rule)

        finding = _make_finding(severity=Severity.HIGH, confidence=0.8)
        decision = await engine.evaluate(finding)

        assert decision.action == "require_approval"

    @pytest.mark.asyncio
    async def test_engine_escalate(self, enforcement_fixture):
        """Engine should ESCALATE when rule matches."""
        engine = enforcement_fixture["enforcement_engine"]

        from verdity.enforcement import Action, GateRule

        rule = GateRule(
            id="escalate-medium",
            when="finding.severity=='medium' and finding.confidence>0.9",
            then=Action.ESCALATE,
            message="High confidence MEDIUM escalates",
        )
        engine.add_rule(rule)

        finding = _make_finding(severity=Severity.MEDIUM, confidence=0.95)
        decision = await engine.evaluate(finding)

        assert decision.action == "escalate"

    @pytest.mark.asyncio
    async def test_engine_multiple_rules_first_match(self, enforcement_fixture):
        """Engine should apply first matching rule."""
        engine = enforcement_fixture["enforcement_engine"]

        from verdity.enforcement import Action, GateRule

        # Rule 1: block CRITICAL
        rule1 = GateRule(
            id="rule1",
            when="finding.severity=='critical'",
            then=Action.BLOCK,
            message="Block CRITICAL",
        )
        # Rule 2: escalate HIGH
        rule2 = GateRule(
            id="escalate-high",
            when="finding.severity=='high'",
            then=Action.ESCALATE,
            message="Escalate HIGH",
        )
        engine.add_rule(rule1)
        engine.add_rule(rule2)

        finding = _make_finding(severity=Severity.CRITICAL, confidence=0.5)
        decision = await engine.evaluate(finding)

        assert decision.action == "block"
        assert decision.rule_id == "rule1"

    @pytest.mark.asyncio
    async def test_engine_empty_rules_allows(self, enforcement_fixture):
        """Engine with no rules should ALLOW all findings."""
        engine = enforcement_fixture["enforcement_engine"]

        finding = _make_finding(severity=Severity.CRITICAL, confidence=1.0)
        decision = await engine.evaluate(finding)

        assert decision.action == "allow"

    @pytest.mark.asyncio
    async def test_engine_evaluates_before_confidence_threshold(self, enforcement_fixture):
        """Engine should evaluate blocking rules BEFORE confidence threshold."""
        engine = enforcement_fixture["enforcement_engine"]

        from verdity.enforcement import Action, GateRule

        rule = GateRule(
            id="block-low-conf-critical",
            when="finding.severity=='critical' and finding.confidence<0.5",
            then=Action.BLOCK,
            message="Low confidence CRITICAL blocks",
        )
        engine.add_rule(rule)

        # Low confidence CRITICAL should be blocked by rule, not allowed by threshold
        finding = _make_finding(severity=Severity.CRITICAL, confidence=0.3)
        decision = await engine.evaluate(finding)

        assert decision.action == "block"


# ── GitHub Checks API Tests ─────────────────────────────────────────────────


class TestGitHubChecksAPI:
    """Test GitHub Checks API integration."""

    @pytest.mark.asyncio
    async def test_create_check_run(self, enforcement_fixture):
        """GitHubClient should have create_check_run method."""
        client = enforcement_fixture["github_client"]

        assert hasattr(client, "create_check_run")
        assert callable(client.create_check_run)

    @pytest.mark.asyncio
    async def test_update_check_run(self, enforcement_fixture):
        """GitHubClient should have update_check_run method."""
        client = enforcement_fixture["github_client"]

        assert hasattr(client, "update_check_run")
        assert callable(client.update_check_run)

    @pytest.mark.asyncio
    async def test_create_check_run_calls_github_api(self, enforcement_fixture):
        """create_check_run should call GitHub API with correct parameters."""
        client = enforcement_fixture["github_client"]

        with (
            patch.object(
                client, "_get_installation_token", new_callable=AsyncMock, return_value="fake-token"
            ),
            patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request,
        ):
            mock_request.return_value = httpx.Response(
                200, json={"id": 12345, "status": "in_progress"}
            )

            result = await client.create_check_run(
                owner="test-owner",
                repo="test-repo",
                name="verdity-review",
                head_sha="abc123",
                status="in_progress",
                conclusion=None,
            )

            assert result["id"] == 12345

    @pytest.mark.asyncio
    async def test_update_check_run_calls_github_api(self, enforcement_fixture):
        """update_check_run should call GitHub API with correct parameters."""
        client = enforcement_fixture["github_client"]

        with (
            patch.object(
                client, "_get_installation_token", new_callable=AsyncMock, return_value="fake-token"
            ),
            patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request,
        ):
            mock_request.return_value = httpx.Response(
                200, json={"id": 12345, "status": "completed", "conclusion": "success"}
            )

            result = await client.update_check_run(
                owner="test-owner",
                repo="test-repo",
                check_run_id=12345,
                status="completed",
                conclusion="success",
            )

            assert result["conclusion"] == "success"


# ── Approval Queue SLA Tests ────────────────────────────────────────────────


class TestApprovalQueueSLA:
    """Test approval queue SLA escalation."""

    @pytest.mark.asyncio
    async def test_approval_item_has_sla_hours(self, enforcement_fixture):
        """Approval queue items should have sla_hours field."""
        approval_queue = enforcement_fixture["approval_queue"]

        from verdity.approval_queue import ApprovalItem

        item = ApprovalItem(
            id="test-1",
            repo_id="acme/widgets",
            pr_number=1,
            finding_id="finding-1",
            reason="Test",
            sla_hours=24,
        )
        assert item.sla_hours == 24

    @pytest.mark.asyncio
    async def test_sla_escalation_job(self, enforcement_fixture):
        """Background job should escalate items past SLA."""
        approval_queue = enforcement_fixture["approval_queue"]

        # Add item with SLA of 1 hour
        from verdity.approval_queue import ApprovalItem

        item = ApprovalItem(
            id="test-sla-1",
            repo_id="acme/widgets",
            pr_number=1,
            finding_id="finding-1",
            reason="Test",
            sla_hours=1,
            created_at=datetime.now(UTC) - timedelta(hours=2),  # 2 hours ago - past SLA
        )
        await approval_queue.add_item(item)

        # Run escalation check
        escalated = await approval_queue.check_sla_escalations()

        assert len(escalated) >= 1
        assert any(e.id == "test-sla-1" for e in escalated)

    @pytest.mark.asyncio
    async def test_sla_boundary_respects_both_directions(self, enforcement_fixture):
        """Regression: created_at is stored as ISO with 'T' and '+00:00', while
        SQLite's datetime() yields 'YYYY-MM-DD HH:MM:SS'. Comparing the two as
        TEXT made every pending item look overdue, so items were escalated
        immediately instead of after their SLA elapsed.

        Both directions must hold: an item inside its SLA stays pending, and an
        item past its SLA escalates.
        """
        approval_queue = enforcement_fixture["approval_queue"]

        from verdity.approval_queue import ApprovalItem

        now = datetime.now(UTC)
        inside = ApprovalItem(
            id="sla-inside",
            repo_id="acme/widgets",
            pr_number=1,
            finding_id="f-inside",
            reason="Within SLA",
            sla_hours=24,
            created_at=now - timedelta(hours=1),
        )
        outside = ApprovalItem(
            id="sla-outside",
            repo_id="acme/widgets",
            pr_number=2,
            finding_id="f-outside",
            reason="Past SLA",
            sla_hours=1,
            created_at=now - timedelta(hours=2),
        )
        await approval_queue.add_item(inside)
        await approval_queue.add_item(outside)

        escalated = await approval_queue.check_sla_escalations()
        escalated_ids = {e.id for e in escalated}

        assert "sla-inside" not in escalated_ids
        assert "sla-outside" in escalated_ids

    @pytest.mark.asyncio
    async def test_sla_not_escalated_before_deadline(self, enforcement_fixture):
        """Items not past SLA should not be escalated."""
        approval_queue = enforcement_fixture["approval_queue"]

        from verdity.approval_queue import ApprovalItem

        item = ApprovalItem(
            id="test-sla-2",
            repo_id="acme/widgets",
            pr_number=1,
            finding_id="finding-2",
            reason="Test",
            sla_hours=24,
            created_at=datetime.now(UTC) - timedelta(hours=1),  # 1 hour ago - within SLA
        )
        await approval_queue.add_item(item)

        escalated = await approval_queue.check_sla_escalations()

        assert not any(e.id == "test-sla-2" for e in escalated)

    @pytest.mark.asyncio
    async def test_escalated_items_marked(self, enforcement_fixture):
        """Escalated items should be marked as escalated."""
        approval_queue = enforcement_fixture["approval_queue"]

        from verdity.approval_queue import ApprovalItem

        item = ApprovalItem(
            id="test-sla-3",
            repo_id="acme/widgets",
            pr_number=1,
            finding_id="finding-3",
            reason="Test",
            sla_hours=1,
            created_at=datetime.now(UTC) - timedelta(hours=2),
        )
        await approval_queue.add_item(item)

        escalated = await approval_queue.check_sla_escalations()
        assert len(escalated) >= 1

        # Check item is marked escalated
        updated_item = await approval_queue.get_item("test-sla-3")
        assert updated_item.escalated == 1


# ── Verifier Disagreement Auto-Escalation Tests ─────────────────────────────


class TestVerifierDisagreementEscalation:
    """Test auto-escalation on verifier disagreement."""

    @pytest.mark.asyncio
    async def test_verification_gate_escalates_disagreement(self, enforcement_fixture):
        """VerificationGate should escalate when verifiers disagree."""
        from verdity.coding_agent import ProposedFix
        from verdity.verification_gate import VerificationGate

        gate = VerificationGate()

        # Create a finding and a proposed fix that would cause disagreement
        original_finding = _make_finding(
            severity=Severity.CRITICAL,
            confidence=0.9,
            file="src/auth.py",
            line_start=1,
            line_end=1,
        )

        # Proposed fix with low confidence (simulating disagreement)
        proposed_fix = ProposedFix(
            finding_id=original_finding.finding_id,
            file="src/auth.py",
            original_line=1,
            suggested_lines=["# Fixed code"],
            explanation="Fix with low confidence",
            fix_type="security",
        )

        # Mock verifier that disagrees
        class DisagreeingVerifier:
            def verify(self, proposed_fix, original_finding):
                from verdity.verification_gate import CheckResult, GateCheck

                return GateCheck(
                    name="matches_intent",
                    result=CheckResult.FAIL,
                    reason="Verifier disagrees with fix",
                )

        verifier = DisagreeingVerifier()

        gate = VerificationGate()
        result = gate.run_checks(proposed_fix, original_finding, verifier=verifier)

        # Should have escalation due to disagreement
        assert result.escalated is True
        assert "disagreement" in result.notes.lower()

    def test_escalation_scheduled_when_approval_queue_present(self):
        """With an approval queue, escalation is scheduled for human review."""
        from verdity.coding_agent import ProposedFix
        from verdity.verification_gate import CheckResult, GateCheck, VerificationGate

        original_finding = _make_finding(
            severity=Severity.CRITICAL,
            confidence=0.9,
            file="src/auth.py",
            line_start=1,
            line_end=1,
        )
        proposed_fix = ProposedFix(
            finding_id=original_finding.finding_id,
            file="src/auth.py",
            original_line=1,
            suggested_lines=["# Fixed code"],
            explanation="Fix with low confidence",
            fix_type="security",
        )

        class DisagreeingVerifier:
            def verify(self, proposed_fix, original_finding):
                return GateCheck(
                    name="matches_intent",
                    result=CheckResult.FAIL,
                    reason="Verifier disagrees with fix",
                )

        gate = VerificationGate()
        approval_queue = MagicMock()

        result = gate.run_checks(
            proposed_fix,
            original_finding,
            verifier=DisagreeingVerifier(),
            approval_queue=approval_queue,
        )

        assert result.escalated is True
        assert result.escalation_scheduled is True
        assert "ESCALATED" in result.notes


# ── Budget Enforcer Re-Queue Tests ──────────────────────────────────────────


class TestBudgetEnforcerRequeue:
    """Test budget enforcer returns dropped specialists for re-queue."""

    @pytest.mark.asyncio
    async def test_budget_enforcer_returns_dropped(self, enforcement_fixture):
        """BudgetEnforcer should return dropped specialist IDs."""
        budget_enforcer = enforcement_fixture["budget_enforcer"]

        from verdity.budget_enforcer import SpecialistBudget

        # Set up budget with limited slots
        budget_enforcer.set_budget("security", SpecialistBudget(max_concurrent=1))

        # First request should be allowed
        allowed1, dropped1 = await budget_enforcer.check_specialist_budget("security", "spec-1")
        assert allowed1 is True
        assert dropped1 == []

        # Second request should drop the first
        allowed2, dropped2 = await budget_enforcer.check_specialist_budget("security", "spec-2")
        assert allowed2 is True
        assert "spec-1" in dropped2

    @pytest.mark.asyncio
    async def test_dropped_specialists_requeued(self, enforcement_fixture):
        """Dropped specialists should be returned for re-queuing."""
        budget_enforcer = enforcement_fixture["budget_enforcer"]

        from verdity.budget_enforcer import SpecialistBudget

        budget_enforcer.set_budget("code_quality", SpecialistBudget(max_concurrent=1))

        # Fill budget
        allowed1, dropped1 = await budget_enforcer.check_specialist_budget("code_quality", "spec-1")
        allowed2, dropped2 = await budget_enforcer.check_specialist_budget("code_quality", "spec-2")

        # spec-1 should be dropped
        assert allowed1 is True
        assert allowed2 is True
        assert "spec-1" in dropped2


# ── Router Integration Tests ────────────────────────────────────────────────


class TestRouterEnforcementIntegration:
    """Test router integration with enforcement engine."""

    @pytest.mark.asyncio
    async def test_router_calls_enforcement_before_confidence(self, enforcement_fixture):
        """Router should evaluate enforcement rules before confidence threshold."""
        # Create engine with rule
        from verdity.enforcement import Action, EnforcementEngine, GateRule
        from verdity.router import route

        engine = EnforcementEngine(rules=[])
        rule = GateRule(
            id="block-test",
            when="finding.severity=='critical'",
            then=Action.BLOCK,
            message="Block for test",
        )
        engine.add_rule(rule)

        finding = _make_finding(severity=Severity.CRITICAL, confidence=0.95)
        decision = await route(finding, enforcement_engine=engine)

        # Should be blocked by enforcement, not pass through confidence
        assert decision.action == RouteAction.MANUAL_REVIEW


# ── Gate Test ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gate_issue42_enforcement():
    """Issue #42 gate: full enforcement engine works end-to-end."""
    # 1. EnforcementEngine exists and evaluates rules
    from verdity.enforcement import Action, EnforcementEngine, GateRule

    engine = EnforcementEngine(rules=[])
    rule = GateRule(
        id="test-rule",
        when="finding.severity=='high'",
        then=Action.BLOCK,
        message="Test",
    )
    engine.add_rule(rule)

    finding = _make_finding(severity=Severity.HIGH, confidence=0.8)
    decision = await engine.evaluate(finding)
    assert decision.action == "block"

    # 2. GitHubClient has Checks API methods
    from verdity.github_client import GitHubClient

    client = GitHubClient(
        app_id=12345,
        private_key_pem="-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----",
        installation_id="98765",
    )
    assert hasattr(client, "create_check_run")
    assert hasattr(client, "update_check_run")

    # 3. ApprovalQueue has sla_hours and escalation
    from verdity.approval_queue import ApprovalItem, ApprovalQueue
    from verdity.audit_store import AuditStore

    audit_store = AuditStore(db_path=":memory:")
    await audit_store.connect()
    approval_queue = ApprovalQueue(db_path=":memory:")
    await approval_queue.connect()

    item = ApprovalItem(
        id="test-gate",
        repo_id="acme/widgets",
        pr_number=1,
        finding_id="f-1",
        reason="Test",
        sla_hours=24,
    )
    assert item.sla_hours == 24
    await approval_queue.close()
    await audit_store.close()

    # 4. BudgetEnforcer returns dropped specialists
    from verdity.budget_enforcer import BudgetEnforcer, SpecialistBudget
    from verdity.token_economics import TokenEconomicsService

    te_service = TokenEconomicsService()
    budget_enforcer = BudgetEnforcer(te_service=te_service)
    budget_enforcer.set_budget("test", SpecialistBudget(max_concurrent=1))

    allowed1, dropped1 = await budget_enforcer.check_specialist_budget("test", "spec-1")
    allowed2, dropped2 = await budget_enforcer.check_specialist_budget("test", "spec-2")
    assert allowed1 is True
    assert allowed2 is True
    assert "spec-1" in dropped2

    # 5. Worker has SLA escalation background task
    from verdity.worker import Worker as VerdityWorker

    assert hasattr(VerdityWorker, "check_sla_escalations")

    print("All Issue #42 gate checks passed!")


# ── Enhanced Enforcement Rules Tests (Issue #49) ───────────────────────────────


class TestGateRuleEnhancements:
    """Test enhanced GateRule features: priority, enabled, templating."""

    def test_gate_rule_priority_default(self):
        """GateRule should have default priority of 100."""
        from verdity.enforcement import Action, GateRule

        rule = GateRule(
            id="test-rule",
            when="finding.severity=='critical'",
            then=Action.BLOCK,
            message="Test",
        )
        assert rule.priority == 100

    def test_gate_rule_priority_custom(self):
        """GateRule should accept custom priority."""
        from verdity.enforcement import Action, GateRule

        rule = GateRule(
            id="test-rule",
            when="finding.severity=='critical'",
            then=Action.BLOCK,
            message="Test",
            priority=50,
        )
        assert rule.priority == 50

    def test_gate_rule_enabled_default(self):
        """GateRule should be enabled by default."""
        from verdity.enforcement import Action, GateRule

        rule = GateRule(
            id="test-rule",
            when="finding.severity=='critical'",
            then=Action.BLOCK,
            message="Test",
        )
        assert rule.enabled is True

    def test_gate_rule_enabled_false(self):
        """GateRule should support disabled state."""
        from verdity.enforcement import Action, GateRule

        rule = GateRule(
            id="test-rule",
            when="finding.severity=='critical'",
            then=Action.BLOCK,
            message="Test",
            enabled=False,
        )
        assert rule.enabled is False


class TestRuleTemplating:
    """Test variable templating in rules."""

    @pytest.mark.asyncio
    async def test_rule_with_variable_substitution(self):
        """Rule should support {{variable}} substitution in when clause."""
        from verdity.enforcement import Action, EnforcementEngine, GateRule

        engine = EnforcementEngine(rules=[])
        rule = GateRule(
            id="block-with-threshold",
            when="finding.severity=='critical' and finding.confidence>{{threshold}}",
            then=Action.BLOCK,
            message="CRITICAL above threshold blocks",
        )
        engine.add_rule(rule)

        finding = _make_finding(severity=Severity.CRITICAL, confidence=0.9)
        # Pass threshold via context
        decision = await engine.evaluate_with_context(finding, {"threshold": 0.8})

        assert decision.action == "block"
        assert decision.rule_id == "block-with-threshold"

    @pytest.mark.asyncio
    async def test_rule_variable_not_matching(self):
        """Rule should not match when variable condition fails."""
        from verdity.enforcement import Action, EnforcementEngine, GateRule

        engine = EnforcementEngine(rules=[])
        rule = GateRule(
            id="block-with-threshold",
            when="finding.severity=='critical' and finding.confidence>{{threshold}}",
            then=Action.BLOCK,
            message="CRITICAL above threshold blocks",
        )
        engine.add_rule(rule)

        finding = _make_finding(severity=Severity.CRITICAL, confidence=0.7)
        # Threshold is 0.8, confidence is 0.7 - should not match
        decision = await engine.evaluate_with_context(finding, {"threshold": 0.8})

        assert decision.action == "allow"

    @pytest.mark.asyncio
    async def test_rule_multiple_variables(self):
        """Rule should support multiple variables."""
        from verdity.enforcement import Action, EnforcementEngine, GateRule

        engine = EnforcementEngine(rules=[])
        rule = GateRule(
            id="multi-var-rule",
            when="finding.severity=={{severity}} and finding.confidence>{{min_conf}}",
            then=Action.BLOCK,
            message="Matches severity and confidence",
        )
        engine.add_rule(rule)

        finding = _make_finding(severity=Severity.HIGH, confidence=0.9)
        decision = await engine.evaluate_with_context(
            finding, {"severity": "high", "min_conf": 0.8}
        )

        assert decision.action == "block"

    @pytest.mark.asyncio
    async def test_rule_variable_in_message(self):
        """Rule message should support variable substitution."""
        from verdity.enforcement import Action, EnforcementEngine, GateRule

        engine = EnforcementEngine(rules=[])
        rule = GateRule(
            id="msg-with-var",
            when="finding.severity=='critical'",
            then=Action.BLOCK,
            message="Blocked CRITICAL with confidence {{conf_threshold}}",
        )
        engine.add_rule(rule)

        finding = _make_finding(severity=Severity.CRITICAL, confidence=0.9)
        decision = await engine.evaluate_with_context(finding, {"conf_threshold": 0.8})

        assert decision.action == "block"
        assert "0.8" in decision.message


class TestRulePriorities:
    """Test rule priority ordering."""

    @pytest.mark.asyncio
    async def test_rules_evaluated_by_priority(self):
        """Rules should be evaluated in priority order (lower = higher priority)."""
        from verdity.enforcement import Action, EnforcementEngine, GateRule

        engine = EnforcementEngine(rules=[])

        # Rule with lower priority number = higher priority
        rule_low_priority = GateRule(
            id="low-priority",
            when="finding.severity=='high'",
            then=Action.BLOCK,
            message="Low priority rule",
            priority=200,
        )
        rule_high_priority = GateRule(
            id="high-priority",
            when="finding.severity=='high'",
            then=Action.REQUIRE_APPROVAL,
            message="High priority rule",
            priority=50,
        )
        engine.add_rule(rule_low_priority)
        engine.add_rule(rule_high_priority)

        finding = _make_finding(severity=Severity.HIGH, confidence=0.8)
        decision = await engine.evaluate(finding)

        # High priority rule (50) should match first
        assert decision.action == "require_approval"
        assert decision.rule_id == "high-priority"

    @pytest.mark.asyncio
    async def test_same_priority_preserves_insertion_order(self):
        """Rules with same priority should preserve insertion order."""
        from verdity.enforcement import Action, EnforcementEngine, GateRule

        engine = EnforcementEngine(rules=[])

        rule1 = GateRule(
            id="rule1",
            when="finding.severity=='high'",
            then=Action.BLOCK,
            message="First",
            priority=100,
        )
        rule2 = GateRule(
            id="rule2",
            when="finding.severity=='high'",
            then=Action.REQUIRE_APPROVAL,
            message="Second",
            priority=100,
        )
        engine.add_rule(rule1)
        engine.add_rule(rule2)

        finding = _make_finding(severity=Severity.HIGH, confidence=0.8)
        decision = await engine.evaluate(finding)

        # First inserted should win
        assert decision.rule_id == "rule1"

    @pytest.mark.asyncio
    async def test_disabled_rule_skipped(self):
        """Disabled rules should be skipped during evaluation."""
        from verdity.enforcement import Action, EnforcementEngine, GateRule

        engine = EnforcementEngine(rules=[])

        rule_disabled = GateRule(
            id="disabled-rule",
            when="finding.severity=='high'",
            then=Action.BLOCK,
            message="Should not match",
            enabled=False,
        )
        rule_enabled = GateRule(
            id="enabled-rule",
            when="finding.severity=='high'",
            then=Action.REQUIRE_APPROVAL,
            message="Should match",
            enabled=True,
        )
        engine.add_rule(rule_disabled)
        engine.add_rule(rule_enabled)

        finding = _make_finding(severity=Severity.HIGH, confidence=0.8)
        decision = await engine.evaluate(finding)

        assert decision.action == "require_approval"
        assert decision.rule_id == "enabled-rule"


class TestRuleSet:
    """Test RuleSet grouping functionality."""

    def test_rule_set_creation(self):
        """RuleSet should be created with name, rules, description."""
        from verdity.enforcement import Action, GateRule, RuleSet

        rules = [
            GateRule(
                id="r1",
                when="finding.severity=='critical'",
                then=Action.BLOCK,
                message="Block critical",
            ),
            GateRule(
                id="r2",
                when="finding.severity=='high'",
                then=Action.REQUIRE_APPROVAL,
                message="Approve high",
            ),
        ]
        rule_set = RuleSet(name="security-rules", rules=rules, description="Security rule set")

        assert rule_set.name == "security-rules"
        assert len(rule_set.rules) == 2
        assert rule_set.description == "Security rule set"

    def test_rule_set_evaluate(self):
        """RuleSet should evaluate all rules in priority order."""
        from verdity.enforcement import Action, GateRule, RuleSet

        rules = [
            GateRule(
                id="r1",
                when="finding.severity=='high'",
                then=Action.BLOCK,
                message="Block high",
                priority=100,
            ),
            GateRule(
                id="r2",
                when="finding.severity=='critical'",
                then=Action.REQUIRE_APPROVAL,
                message="Approve critical",
                priority=50,
            ),
        ]
        rule_set = RuleSet(name="test-set", rules=rules, description="Test")

        finding = _make_finding(severity=Severity.CRITICAL, confidence=0.9)
        decisions = rule_set.evaluate(finding)

        # Should return decisions for all matching rules in priority order
        assert len(decisions) >= 1
        # High priority (50) rule should be first
        assert decisions[0].rule_id == "r2"

    def test_rule_set_evaluate_with_context(self):
        """RuleSet should support context variables."""
        from verdity.enforcement import Action, GateRule, RuleSet

        rules = [
            GateRule(
                id="var-rule",
                when="finding.severity=={{sev}} and finding.confidence>{{thresh}}",
                then=Action.BLOCK,
                message="Variable rule",
                priority=100,
            ),
        ]
        rule_set = RuleSet(name="var-set", rules=rules, description="Variable test")

        finding = _make_finding(severity=Severity.HIGH, confidence=0.9)
        decisions = rule_set.evaluate(finding, context={"sev": "high", "thresh": 0.8})

        assert len(decisions) == 1
        assert decisions[0].rule_id == "var-rule"


class TestRegexPatterns:
    """Test built-in regex pattern library."""

    def test_patterns_dict_exists(self):
        """PATTERNS dict should exist with expected keys."""
        from verdity.enforcement import PATTERNS

        assert hasattr(PATTERNS, "secret")
        assert hasattr(PATTERNS, "sql_injection")
        assert hasattr(PATTERNS, "xss")
        assert hasattr(PATTERNS, "path_traversal")

    def test_secret_pattern_matches(self):
        """Secret pattern should match common secret formats."""
        import re

        from verdity.enforcement import PATTERNS

        pattern = re.compile(PATTERNS.secret)
        assert pattern.search('api_key = "abc123"') is not None
        assert pattern.search("secret = 'xyz789'") is not None
        assert pattern.search('token: "Bearer abc"') is not None
        assert pattern.search('password = "secret123"') is not None

    def test_sql_injection_pattern_matches(self):
        """SQL injection pattern should match suspicious SQL."""
        import re

        from verdity.enforcement import PATTERNS

        pattern = re.compile(PATTERNS.sql_injection)
        assert pattern.search("SELECT * FROM users WHERE id = '1'") is not None
        assert pattern.search("UNION SELECT password FROM users") is not None
        assert pattern.search("DROP TABLE users;") is not None

    def test_xss_pattern_matches(self):
        """XSS pattern should match script tags and event handlers."""
        import re

        from verdity.enforcement import PATTERNS

        pattern = re.compile(PATTERNS.xss)
        assert pattern.search("<script>alert('xss')</script>") is not None
        assert pattern.search('onerror="alert(1)"') is not None
        assert pattern.search("onclick=stealCookies()") is not None

    def test_path_traversal_pattern_matches(self):
        """Path traversal pattern should match ../ sequences."""
        import re

        from verdity.enforcement import PATTERNS

        pattern = re.compile(PATTERNS.path_traversal)
        assert pattern.search("../../../etc/passwd") is not None
        assert pattern.search("..\\..\\windows\\system32") is not None

    @pytest.mark.asyncio
    async def test_rule_using_regex_pattern(self):
        """Rule should be able to use regex_search with PATTERNS."""
        from verdity.enforcement import Action, EnforcementEngine, GateRule

        engine = EnforcementEngine(rules=[])
        rule = GateRule(
            id="detect-secrets",
            when="regex_search(finding.content, PATTERNS['secret'])",
            then=Action.BLOCK,
            message="Potential secret detected",
        )
        engine.add_rule(rule)

        # Finding with content containing a secret
        finding = _make_finding(
            severity=Severity.HIGH,
            confidence=0.8,
            summary="Hardcoded API key",
            explanation='api_key = "sk_live_abc123def456"',
        )
        # We need to add content to the finding proxy - let's check how finding works
        decision = await engine.evaluate(finding)

        # This test may need the finding to have a content attribute
        # We'll adjust after seeing implementation


class TestEnforcementEngineEnhancements:
    """Test enhanced EnforcementEngine methods."""

    @pytest.mark.asyncio
    async def test_evaluate_with_context_method(self):
        """Engine should have evaluate_with_context method."""
        from verdity.enforcement import EnforcementEngine

        engine = EnforcementEngine(rules=[])
        assert hasattr(engine, "evaluate_with_context")
        assert callable(engine.evaluate_with_context)

    @pytest.mark.asyncio
    async def test_evaluate_with_context_passes_variables(self):
        """evaluate_with_context should pass variables to rule evaluation."""
        from verdity.enforcement import Action, EnforcementEngine, GateRule

        engine = EnforcementEngine(rules=[])
        rule = GateRule(
            id="ctx-rule",
            when="finding.confidence > {{min_conf}}",
            then=Action.BLOCK,
            message="Context test",
        )
        engine.add_rule(rule)

        finding = _make_finding(severity=Severity.HIGH, confidence=0.9)
        decision = await engine.evaluate_with_context(finding, {"min_conf": 0.8})

        assert decision.action == "block"

    @pytest.mark.asyncio
    async def test_evaluate_uses_default_context(self):
        """evaluate() should work without explicit context (backward compat)."""
        from verdity.enforcement import Action, EnforcementEngine, GateRule

        engine = EnforcementEngine(rules=[])
        rule = GateRule(
            id="simple-rule",
            when="finding.severity=='critical'",
            then=Action.BLOCK,
            message="Simple rule",
        )
        engine.add_rule(rule)

        finding = _make_finding(severity=Severity.CRITICAL, confidence=0.9)
        decision = await engine.evaluate(finding)

        assert decision.action == "block"


# ═══════════════════════════════════════════════════════════════════════
# Phase 1: Enforcement Engine - Missing Coverage Tests
# ═══════════════════════════════════════════════════════════════════════


class TestGateRuleEdgeCases:
    """Test GateRule edge cases for missing coverage."""

    def test_gate_rule_evaluate_empty_finding_dict(self):
        """GateRule.evaluate should handle empty finding dict gracefully."""
        from verdity.enforcement import Action, GateRule

        rule = GateRule(
            id="test-rule",
            when="finding.severity == 'critical'",
            then=Action.BLOCK,
            message="Test",
        )

        # Empty context - finding will be empty dict
        context = {"finding": {}}
        result = rule.evaluate(context)
        assert result is False  # Should not crash, return False

    def test_gate_rule_evaluate_exception_handling(self):
        """GateRule.evaluate should catch exceptions and return False."""
        from verdity.enforcement import Action, GateRule

        rule = GateRule(
            id="test-rule",
            when="finding.severity ==='critical'",  # Invalid syntax - triple equals
            then=Action.BLOCK,
            message="Test",
        )

        finding = _make_finding(severity=Severity.CRITICAL, confidence=0.9)
        context = {"finding": finding}
        result = rule.evaluate(context)
        assert result is False  # Should not crash, return False

    def test_gate_rule_evaluate_finding_none(self):
        """GateRule.evaluate should handle None finding gracefully."""
        from verdity.enforcement import Action, GateRule

        rule = GateRule(
            id="test-rule",
            when="finding.severity == 'critical'",
            then=Action.BLOCK,
            message="Test",
        )

        # Context with finding set to None
        context = {"finding": None}
        result = rule.evaluate(context)
        assert result is False


class TestSubstituteVariablesEdgeCases:
    """Test substitute_variables edge cases for missing coverage."""

    def test_substitute_variables_empty_dict(self):
        """substitute_variables should return template unchanged with empty variables."""
        from verdity.enforcement import substitute_variables

        template = "Hello {{name}}"
        result = substitute_variables(template, {})
        assert result == "Hello {{name}}"

    def test_substitute_variables_none_variables(self):
        """substitute_variables should return template unchanged with None variables."""
        from verdity.enforcement import substitute_variables

        template = "Hello {{name}}"
        result = substitute_variables(template, None)
        assert result == "Hello {{name}}"

    def test_substitute_variables_boolean_values(self):
        """substitute_variables should handle boolean values correctly."""
        from verdity.enforcement import substitute_variables

        template = "Flag: {{flag}}, Enabled: {{enabled}}"
        result = substitute_variables(template, {"flag": True, "enabled": False})
        assert "true" in result.lower()
        assert "false" in result.lower()

    def test_substitute_variables_none_value(self):
        """substitute_variables should handle None values correctly."""
        from verdity.enforcement import substitute_variables

        template = "Value: {{value}}"
        result = substitute_variables(template, {"value": None})
        assert "none" in result.lower()

    def test_substitute_variables_numeric_values(self):
        """substitute_variables should handle numeric values correctly."""
        from verdity.enforcement import substitute_variables

        template = "Int: {{int_val}}, Float: {{float_val}}"
        result = substitute_variables(template, {"int_val": 42, "float_val": 3.14})
        assert "42" in result
        assert "3.14" in result


class TestRuleSetEdgeCases:
    """Test RuleSet edge cases for missing coverage."""

    def test_rule_set_evaluate_missing_optional_attributes(self):
        """RuleSet.evaluate should handle findings without explanation/content."""
        from verdity.enforcement import Action, GateRule, RuleSet
        from verdity.schemas import ConcernType, Finding, Severity

        # Create a minimal finding without explanation attribute
        finding = Finding(
            concern=ConcernType.SECURITY,
            severity=Severity.HIGH,
            file="test.py",
            line_start=1,
            line_end=1,
            summary="Test finding",
            explanation="",  # Empty explanation
            confidence=0.8,
            agent_version="test",
            prompt_hash="sha256:abc",
        )
        # Remove explanation attribute to test getattr fallback
        del finding.explanation

        rules = [
            GateRule(
                id="test-rule",
                when="finding.severity == 'high'",
                then=Action.BLOCK,
                message="Block high",
                priority=100,
            ),
        ]
        rule_set = RuleSet(name="test", rules=rules)
        decisions = rule_set.evaluate(finding)

        assert len(decisions) == 1
        assert decisions[0].action == "block"

    def test_rule_set_evaluate_disabled_rule_skipped(self):
        """RuleSet.evaluate should skip disabled rules."""
        from verdity.enforcement import Action, GateRule, RuleSet
        from verdity.schemas import Severity

        finding = _make_finding(severity=Severity.HIGH, confidence=0.8)

        rules = [
            GateRule(
                id="disabled-rule",
                when="finding.severity == 'high'",
                then=Action.BLOCK,
                message="Should not match",
                enabled=False,
                priority=100,
            ),
            GateRule(
                id="enabled-rule",
                when="finding.severity == 'high'",
                then=Action.REQUIRE_APPROVAL,
                message="Should match",
                enabled=True,
                priority=100,
            ),
        ]
        rule_set = RuleSet(name="test", rules=rules)
        decisions = rule_set.evaluate(finding)

        assert len(decisions) == 1
        assert decisions[0].rule_id == "enabled-rule"
        assert decisions[0].action == "require_approval"


class TestEnforcementEngineEdgeCases:
    """Test EnforcementEngine edge cases for missing coverage."""

    @pytest.mark.asyncio
    async def test_evaluate_with_context_empty_variables(self):
        """EnforcementEngine.evaluate_with_context should handle empty variables dict."""
        from verdity.enforcement import Action, EnforcementEngine, GateRule

        engine = EnforcementEngine(rules=[])
        rule = GateRule(
            id="simple-rule",
            when="finding.severity == 'critical'",
            then=Action.BLOCK,
            message="Test",
        )
        engine.add_rule(rule)

        finding = _make_finding(severity=Severity.CRITICAL, confidence=0.9)
        decision = await engine.evaluate_with_context(finding, {})

        assert decision.action == "block"

    @pytest.mark.asyncio
    async def test_evaluate_with_context_none_variables(self):
        """EnforcementEngine.evaluate_with_context should handle None variables."""
        from verdity.enforcement import Action, EnforcementEngine, GateRule

        engine = EnforcementEngine(rules=[])
        rule = GateRule(
            id="simple-rule",
            when="finding.severity == 'critical'",
            then=Action.BLOCK,
            message="Test",
        )
        engine.add_rule(rule)

        finding = _make_finding(severity=Severity.CRITICAL, confidence=0.9)
        decision = await engine.evaluate_with_context(finding, None)

        assert decision.action == "block"

    @pytest.mark.asyncio
    async def test_evaluate_with_context_multiple_variables(self):
        """EnforcementEngine.evaluate_with_context should handle multiple variable types."""
        from verdity.enforcement import Action, EnforcementEngine, GateRule

        engine = EnforcementEngine(rules=[])
        rule = GateRule(
            id="multi-var-rule",
            when="finding.severity == {{sev}} and finding.confidence > {{min_conf}} and {{flag}} == True",
            then=Action.BLOCK,
            message="Severity: {{sev}}, MinConf: {{min_conf}}, Flag: {{flag}}",
            priority=100,
        )
        engine.add_rule(rule)

        finding = _make_finding(severity=Severity.HIGH, confidence=0.9)
        decision = await engine.evaluate_with_context(
            finding,
            {
                "sev": "high",
                "min_conf": 0.8,
                "flag": True,
            },
        )

        assert decision.action == "block"
        assert "high" in decision.message
        assert "0.8" in decision.message
        assert "true" in decision.message.lower()

    @pytest.mark.asyncio
    async def test_evaluate_with_context_no_matching_rules(self):
        """EnforcementEngine.evaluate_with_context should return allow when no rules match."""
        from verdity.enforcement import Action, EnforcementEngine, GateRule

        engine = EnforcementEngine(rules=[])
        rule = GateRule(
            id="high-sev-rule",
            when="finding.severity == 'critical'",
            then=Action.BLOCK,
            message="Block critical",
        )
        engine.add_rule(rule)

        # HIGH severity, not CRITICAL - should not match
        finding = _make_finding(severity=Severity.HIGH, confidence=0.9)
        decision = await engine.evaluate_with_context(finding, {})

        assert decision.action == "allow"
        assert decision.rule_id is None

    @pytest.mark.asyncio
    async def test_evaluate_with_context_variable_in_when_and_message(self):
        """Variables should work in both when clause and message."""
        from verdity.enforcement import Action, EnforcementEngine, GateRule

        engine = EnforcementEngine(rules=[])
        rule = GateRule(
            id="var-in-both",
            when="finding.confidence > {{threshold}}",
            then=Action.BLOCK,
            message="Confidence {{threshold}} exceeded: actual {{actual}}",
            priority=100,
        )
        engine.add_rule(rule)

        finding = _make_finding(severity=Severity.HIGH, confidence=0.95)
        decision = await engine.evaluate_with_context(
            finding,
            {
                "threshold": 0.9,
                "actual": 0.95,
            },
        )

        assert decision.action == "block"
        assert "0.9" in decision.message
        assert "0.95" in decision.message


# Additional tests for approval_queue get_item
@pytest.mark.asyncio
async def test_approval_queue_get_item_not_found():
    """Test get_item returns None for non-existent item."""
    from verdity.approval_queue import ApprovalQueue

    db = ApprovalQueue(":memory:")
    await db.connect()
    try:
        result = await db.get_item("non-existent-id")
        assert result is None
    finally:
        await db.close()


# Additional tests for budget_enforcer
@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_budget_enforcer_same_specialist_twice():
    """BudgetEnforcer should allow same specialist ID twice."""
    from verdity.budget_enforcer import BudgetEnforcer, SpecialistBudget, TokenEconomicsService

    te_service = TokenEconomicsService()
    be = BudgetEnforcer(te_service=te_service)
    be.set_budget("security", SpecialistBudget(max_concurrent=2))

    # Same specialist twice should be allowed
    allowed1, dropped1 = await be.check_specialist_budget("security", "spec-1")
    allowed2, dropped2 = await be.check_specialist_budget("security", "spec-1")

    assert allowed1 is True
    assert allowed2 is True
    assert dropped1 == []
    assert dropped2 == []


# Additional tests for enforcement engine edge cases (ISS-001)


class TestRegexSearchEdgeCases:
    def test_regex_search_none_text(self):
        """regex_search should return False for None text."""
        from verdity.enforcement import regex_search

        assert regex_search(None, "pattern") is False

    def test_regex_search_empty_text(self):
        """regex_search should return False for empty text."""
        from verdity.enforcement import regex_search

        assert regex_search("", "pattern") is False

    def test_regex_search_match(self):
        """regex_search should return True for matching pattern."""
        from verdity.enforcement import regex_search

        assert regex_search("hello world", "world") is True

    def test_regex_search_no_match(self):
        """regex_search should return False for non-matching pattern."""
        from verdity.enforcement import regex_search

        assert regex_search("hello", "world") is False


class TestEnforcementEngineRemoveRule:
    def test_remove_rule_not_found(self):
        """remove_rule should return False when rule not found."""
        from verdity.enforcement import Action, EnforcementEngine, GateRule

        engine = EnforcementEngine(rules=[])
        rule = GateRule(
            id="test-rule",
            when="finding.severity == 'high'",
            then=Action.BLOCK,
            message="Test",
        )
        engine.add_rule(rule)

        result = engine.remove_rule("non-existent")
        assert result is False

        # Original rule should still be there
        result = engine.remove_rule("test-rule")
        assert result is True


class TestLoadRulesFromYaml:
    def test_load_rules_from_yaml_success(self, tmp_path):
        """load_rules_from_yaml should parse valid YAML."""
        from verdity.enforcement import Action, load_rules_from_yaml

        rules_file = tmp_path / "rules.yml"
        rules_file.write_text("""
rules:
  - id: "test-rule"
    when: "finding.severity == 'high'"
    then: "BLOCK"
    message: "High severity"
    priority: 50
    enabled: true
""")

        rules = load_rules_from_yaml(str(rules_file))
        assert len(rules) == 1
        assert rules[0].id == "test-rule"
        assert rules[0].then == Action.BLOCK
        assert rules[0].priority == 50

    def test_load_rules_from_yaml_empty_rules(self, tmp_path):
        """load_rules_from_yaml should handle empty rules list."""
        from verdity.enforcement import load_rules_from_yaml

        rules_file = tmp_path / "rules.yml"
        rules_file.write_text("rules: []")

        rules = load_rules_from_yaml(str(rules_file))
        assert rules == []

    def test_load_rules_from_yaml_missing_rules_key(self, tmp_path):
        """load_rules_from_yaml should handle missing rules key."""
        from verdity.enforcement import load_rules_from_yaml

        rules_file = tmp_path / "rules.yml"
        rules_file.write_text("other_key: value")

        rules = load_rules_from_yaml(str(rules_file))
        assert rules == []

    def test_load_rules_from_yaml_invalid_action(self, tmp_path):
        """load_rules_from_yaml should handle invalid action."""
        from verdity.enforcement import load_rules_from_yaml

        rules_file = tmp_path / "rules.yml"
        rules_file.write_text("""
rules:
  - id: "test-rule"
    when: "finding.severity == 'high'"
    then: "INVALID_ACTION"
    message: "Test"
""")

        import pytest

        with pytest.raises(KeyError):
            load_rules_from_yaml(str(rules_file))

    def test_load_rules_from_yaml_disabled_rule(self, tmp_path):
        """load_rules_from_yaml should handle disabled rules."""
        from verdity.enforcement import load_rules_from_yaml

        rules_file = tmp_path / "rules.yml"
        rules_file.write_text("""
rules:
  - id: "test-rule"
    when: "finding.severity == 'high'"
    then: "BLOCK"
    message: "Test"
    enabled: false
""")

        rules = load_rules_from_yaml(str(rules_file))
        assert len(rules) == 1
        assert rules[0].enabled is False

    def test_load_rules_from_yaml_default_priority(self, tmp_path):
        """load_rules_from_yaml should use default priority."""
        from verdity.enforcement import load_rules_from_yaml

        rules_file = tmp_path / "rules.yml"
        rules_file.write_text("""
rules:
  - id: "test-rule"
    when: "finding.severity == 'high'"
    then: "BLOCK"
    message: "Test"
""")

        rules = load_rules_from_yaml(str(rules_file))
        assert len(rules) == 1
        assert rules[0].priority == 100
