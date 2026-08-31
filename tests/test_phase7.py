"""
Tests for Phase 7: Budget Enforcement and Dashboard.
"""

from __future__ import annotations

import uuid

import pytest

from verdity.budget_enforcer import BudgetEnforcer, DegradationSignal
from verdity.token_economics import TokenEconomicsService


@pytest.fixture
async def te_service():
    te = TokenEconomicsService(db_path=":memory:")
    await te.connect()
    yield te
    await te.close()


@pytest.fixture
def enforcer(te_service):
    return BudgetEnforcer(te_service)


class TestBudgetEnforcement:
    @pytest.mark.asyncio
    async def test_within_budget_no_degradation(self, te_service, enforcer):
        # Record a small amount of spend
        await te_service.record_call(
            review_run_id=uuid.uuid4(),
            agent_name="security",
            model="deepseek-chat",
            input_tokens=100,
            output_tokens=50,
            repo_owner="acme",
            repo_name="widgets",
            org="acme",
        )
        status = await enforcer.check_budget(
            repo_owner="acme",
            repo_name="widgets",
            budget_usd=100.0,  # very generous
            current_specialists=["security", "code_quality", "testing", "documentation"],
        )
        assert status.signal == DegradationSignal.NORMAL
        assert status.spend_usd > 0
        assert status.ratio < 0.6

    @pytest.mark.asyncio
    async def test_warn_threshold_triggers_optional_drop(self, te_service, enforcer):
        # Record spend that hits the degrade threshold
        # deepseek-chat: input $0.14/M, output $0.28/M
        # Each call: 500K in + 500K out = $0.07 + $0.14 = $0.21
        for _ in range(10):
            await te_service.record_call(
                review_run_id=uuid.uuid4(),
                agent_name="security",
                model="deepseek-chat",
                input_tokens=500_000,
                output_tokens=500_000,
                repo_owner="acme",
                repo_name="widgets",
                org="acme",
            )
        # Spend ≈ $2.10. Budget of $3.00 → ratio 0.70 → degrade_optional
        status = await enforcer.check_budget(
            repo_owner="acme",
            repo_name="widgets",
            budget_usd=3.00,
            current_specialists=["security", "documentation"],
        )
        assert status.signal == DegradationSignal.DEGRADE_OPTIONAL
        assert "documentation" in status.dropped_specialists
        assert "security" not in status.dropped_specialists

    @pytest.mark.asyncio
    async def test_halt_threshold_drops_all(self, te_service, enforcer):
        # Accumulate enough spend to hit 100% of a small budget
        for _ in range(50):
            await te_service.record_call(
                review_run_id=uuid.uuid4(),
                agent_name="security",
                model="deepseek-chat",
                input_tokens=500_000,
                output_tokens=500_000,
                repo_owner="acme",
                repo_name="small-budget",
                org="acme",
            )
        status = await enforcer.check_budget(
            repo_owner="acme",
            repo_name="small-budget",
            budget_usd=0.10,  # $0.10 budget, we've spent much more
            current_specialists=["security", "code_quality"],
        )
        assert status.signal == DegradationSignal.HALT
        assert status.ratio >= 1.0

    @pytest.mark.asyncio
    async def test_zero_budget_means_unlimited(self, te_service, enforcer):
        status = await enforcer.check_budget(
            repo_owner="acme",
            repo_name="widgets",
            budget_usd=0.0,  # unlimited
            current_specialists=["security"],
        )
        assert status.signal == DegradationSignal.NORMAL
        assert status.spend_usd == 0.0

    @pytest.mark.asyncio
    async def test_security_never_dropped_first(self, te_service, enforcer):
        # Even at high spend, security should be the last to go
        for _ in range(20):
            await te_service.record_call(
                review_run_id=uuid.uuid4(),
                agent_name="docs",
                model="deepseek-chat",
                input_tokens=500_000,
                output_tokens=500_000,
                repo_owner="acme",
                repo_name="widgets",
                org="acme",
            )
        status = await enforcer.check_budget(
            repo_owner="acme",
            repo_name="widgets",
            budget_usd=0.50,
            current_specialists=["security", "documentation"],
        )
        # Security should NEVER appear in dropped_specialists, even at HALT
        assert "security" not in status.dropped_specialists
        # Documentation (optional) should be dropped
        assert "documentation" in status.dropped_specialists
