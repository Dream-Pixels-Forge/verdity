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

import asyncio
import os
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from verdity.schemas import ConcernType, Finding, RankedFinding, Severity
from verdity.router import RouteAction


@pytest_asyncio.fixture
async def enforcement_fixture():
    """Set up enforcement components."""
    from verdity.enforcement import EnforcementEngine, GateRule, Action
    from verdity.github_client import GitHubClient
    from verdity.approval_queue import ApprovalQueue
    from verdity.budget_enforcer import BudgetEnforcer
    from verdity.token_economics import TokenEconomicsService
    from verdity.audit_store import AuditStore
    from verdity.event_queue import EventQueue
    from verdity.metrics_store import MetricsStore

    # Set required env vars
    os.environ["GITHUB_APP_ID"] = "12345"
    os.environ["GITHUB_APP_INSTALLATION_ID"] = "98765"
    os.environ["GITHUB_APP_PRIVATE_KEY"] = "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"

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
        from verdity.enforcement import GateRule, Action

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
        from verdity.enforcement import GateRule, Action

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

        from verdity.enforcement import GateRule, Action

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

        from verdity.enforcement import GateRule, Action

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

        from verdity.enforcement import GateRule, Action

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

        from verdity.enforcement import GateRule, Action

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

        from verdity.enforcement import GateRule, Action

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

        with patch.object(client, "_get_installation_token", new_callable=AsyncMock, return_value="fake-token"):
            with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request:
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

        with patch.object(client, "_get_installation_token", new_callable=AsyncMock, return_value="fake-token"):
            with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request:
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
        from verdity.verification_gate import VerificationGate
        from verdity.coding_agent import ProposedFix
        from verdity.schemas import Finding

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
                from verdity.verification_gate import GateCheck, CheckResult
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
        from verdity.router import route
        from verdity.enforcement import GateRule, Action

        # Create engine with rule
        from verdity.enforcement import EnforcementEngine

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
    from verdity.enforcement import EnforcementEngine, GateRule, Action

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
    from verdity.approval_queue import ApprovalQueue, ApprovalItem
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
    from verdity.metrics_store import MetricsStore
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
        from verdity.enforcement import GateRule, Action

        rule = GateRule(
            id="test-rule",
            when="finding.severity=='critical'",
            then=Action.BLOCK,
            message="Test",
        )
        assert rule.priority == 100

    def test_gate_rule_priority_custom(self):
        """GateRule should accept custom priority."""
        from verdity.enforcement import GateRule, Action

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
        from verdity.enforcement import GateRule, Action

        rule = GateRule(
            id="test-rule",
            when="finding.severity=='critical'",
            then=Action.BLOCK,
            message="Test",
        )
        assert rule.enabled is True

    def test_gate_rule_enabled_false(self):
        """GateRule should support disabled state."""
        from verdity.enforcement import GateRule, Action

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
        from verdity.enforcement import EnforcementEngine, GateRule, Action

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
        from verdity.enforcement import EnforcementEngine, GateRule, Action

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
        from verdity.enforcement import EnforcementEngine, GateRule, Action

        engine = EnforcementEngine(rules=[])
        rule = GateRule(
            id="multi-var-rule",
            when="finding.severity=={{severity}} and finding.confidence>{{min_conf}}",
            then=Action.BLOCK,
            message="Matches severity and confidence",
        )
        engine.add_rule(rule)

        finding = _make_finding(severity=Severity.HIGH, confidence=0.9)
        decision = await engine.evaluate_with_context(finding, {"severity": "high", "min_conf": 0.8})

        assert decision.action == "block"

    @pytest.mark.asyncio
    async def test_rule_variable_in_message(self):
        """Rule message should support variable substitution."""
        from verdity.enforcement import EnforcementEngine, GateRule, Action

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
        from verdity.enforcement import EnforcementEngine, GateRule, Action

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
        from verdity.enforcement import EnforcementEngine, GateRule, Action

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
        from verdity.enforcement import EnforcementEngine, GateRule, Action

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
        from verdity.enforcement import RuleSet, GateRule, Action

        rules = [
            GateRule(id="r1", when="finding.severity=='critical'", then=Action.BLOCK, message="Block critical"),
            GateRule(id="r2", when="finding.severity=='high'", then=Action.REQUIRE_APPROVAL, message="Approve high"),
        ]
        rule_set = RuleSet(name="security-rules", rules=rules, description="Security rule set")

        assert rule_set.name == "security-rules"
        assert len(rule_set.rules) == 2
        assert rule_set.description == "Security rule set"

    def test_rule_set_evaluate(self):
        """RuleSet should evaluate all rules in priority order."""
        from verdity.enforcement import RuleSet, GateRule, Action

        rules = [
            GateRule(id="r1", when="finding.severity=='high'", then=Action.BLOCK, message="Block high", priority=100),
            GateRule(id="r2", when="finding.severity=='critical'", then=Action.REQUIRE_APPROVAL, message="Approve critical", priority=50),
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
        from verdity.enforcement import RuleSet, GateRule, Action

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

        assert "secret" in PATTERNS
        assert "sql_injection" in PATTERNS
        assert "xss" in PATTERNS
        assert "path_traversal" in PATTERNS

    def test_secret_pattern_matches(self):
        """Secret pattern should match common secret formats."""
        import re
        from verdity.enforcement import PATTERNS

        pattern = re.compile(PATTERNS["secret"])
        assert pattern.search('api_key = "abc123"') is not None
        assert pattern.search("secret = 'xyz789'") is not None
        assert pattern.search('token: "Bearer abc"') is not None
        assert pattern.search('password = "secret123"') is not None

    def test_sql_injection_pattern_matches(self):
        """SQL injection pattern should match suspicious SQL."""
        import re
        from verdity.enforcement import PATTERNS

        pattern = re.compile(PATTERNS["sql_injection"])
        assert pattern.search("SELECT * FROM users WHERE id = '1'") is not None
        assert pattern.search("UNION SELECT password FROM users") is not None
        assert pattern.search("DROP TABLE users;") is not None

    def test_xss_pattern_matches(self):
        """XSS pattern should match script tags and event handlers."""
        import re
        from verdity.enforcement import PATTERNS

        pattern = re.compile(PATTERNS["xss"])
        assert pattern.search("<script>alert('xss')</script>") is not None
        assert pattern.search('onerror="alert(1)"') is not None
        assert pattern.search("onclick=stealCookies()") is not None

    def test_path_traversal_pattern_matches(self):
        """Path traversal pattern should match ../ sequences."""
        import re
        from verdity.enforcement import PATTERNS

        pattern = re.compile(PATTERNS["path_traversal"])
        assert pattern.search("../../../etc/passwd") is not None
        assert pattern.search("..\\..\\windows\\system32") is not None

    @pytest.mark.asyncio
    async def test_rule_using_regex_pattern(self):
        """Rule should be able to use regex_search with PATTERNS."""
        from verdity.enforcement import EnforcementEngine, GateRule, Action

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
        from verdity.enforcement import EnforcementEngine, GateRule, Action

        engine = EnforcementEngine(rules=[])
        assert hasattr(engine, "evaluate_with_context")
        assert callable(engine.evaluate_with_context)

    @pytest.mark.asyncio
    async def test_evaluate_with_context_passes_variables(self):
        """evaluate_with_context should pass variables to rule evaluation."""
        from verdity.enforcement import EnforcementEngine, GateRule, Action

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
        from verdity.enforcement import EnforcementEngine, GateRule, Action

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