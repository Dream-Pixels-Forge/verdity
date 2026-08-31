"""
Tests for Token Economics Service.
"""

from __future__ import annotations

import pytest

from verdity.token_economics import TokenEconomicsService, estimate_cost


@pytest.mark.asyncio
async def test_record_and_sum_spend(token_economics: TokenEconomicsService):
    import uuid

    run_id = uuid.uuid4()
    await token_economics.record_call(
        review_run_id=run_id,
        agent_name="security-agent",
        model="gpt-4o",
        input_tokens=5000,
        output_tokens=500,
        repo_owner="acme",
        repo_name="widgets",
    )
    stats = await token_economics.get_spend(repo_owner="acme", repo_name="widgets")
    assert stats["total_calls"] == 1
    assert stats["tokens_in"] == 5000
    assert stats["tokens_out"] == 500
    assert stats["spend_usd"] > 0


@pytest.mark.asyncio
async def test_budget_enforcement_within_range(token_economics: TokenEconomicsService):
    result = await token_economics.check_budget_enforcement(
        repo_owner="acme",
        repo_name="widgets",
        budget_usd=100.0,
    )
    assert result["within_budget"] is True
    assert result["degrade_signal"] is None


@pytest.mark.asyncio
async def test_budget_enforcement_halt(token_economics: TokenEconomicsService):
    import uuid

    # Accumulate spend above budget
    run_id = uuid.uuid4()
    await token_economics.record_call(
        review_run_id=run_id,
        agent_name="security-agent",
        model="gpt-4o",
        input_tokens=50_000_000,  # 50M input tokens ≈ $125 at gpt-4o rates
        output_tokens=0,
        repo_owner="acme",
        repo_name="widgets",
    )
    result = await token_economics.check_budget_enforcement(
        repo_owner="acme",
        repo_name="widgets",
        budget_usd=100.0,
    )
    assert result["within_budget"] is False
    assert result["degrade_signal"] == "halt"


@pytest.mark.asyncio
async def test_zero_budget_means_unlimited(token_economics: TokenEconomicsService):
    result = await token_economics.check_budget_enforcement(
        repo_owner="acme",
        repo_name="widgets",
        budget_usd=0.0,
    )
    assert result["within_budget"] is True


@pytest.fixture
async def token_economics():
    svc = TokenEconomicsService(db_path=":memory:")
    await svc.connect()
    yield svc
    await svc.close()


class TestEstimateCost:
    def test_known_model(self):
        # gpt-4o: input $2.50/M, output $10.00/M
        cost = estimate_cost("gpt-4o", 1_000_000, 100_000)
        assert cost == pytest.approx(2.50 + 1.00, abs=0.01)

    def test_zero_tokens(self):
        cost = estimate_cost("claude-sonnet-4-20250514", 0, 0)
        assert cost == 0.0

    def test_unknown_model_uses_default(self):
        # Unknown model falls back to $5/M combined
        cost = estimate_cost("unknown-model", 1_000_000, 0)
        assert cost == pytest.approx(5.0, abs=0.01)


@pytest.mark.asyncio
async def test_get_spend_by_org(token_economics: TokenEconomicsService):
    import uuid

    await token_economics.record_call(
        review_run_id=uuid.uuid4(),
        agent_name="sec",
        model="m",
        input_tokens=1000,
        output_tokens=500,
        repo_owner="acme",
        repo_name="w1",
        org="acme-org",
    )
    await token_economics.record_call(
        review_run_id=uuid.uuid4(),
        agent_name="sec",
        model="m",
        input_tokens=2000,
        output_tokens=1000,
        repo_owner="acme",
        repo_name="w2",
        org="acme-org",
    )
    await token_economics.record_call(
        review_run_id=uuid.uuid4(),
        agent_name="sec",
        model="m",
        input_tokens=500,
        output_tokens=200,
        repo_owner="other",
        repo_name="x",
        org="other-org",
    )
    result = await token_economics.get_spend_by_org()
    assert len(result) >= 2
    org_names = {r["org"] for r in result}
    assert "acme-org" in org_names
    assert "other-org" in org_names


@pytest.mark.asyncio
async def test_get_spend_by_repo(token_economics: TokenEconomicsService):
    import uuid

    await token_economics.record_call(
        review_run_id=uuid.uuid4(),
        agent_name="sec",
        model="m",
        input_tokens=1000,
        output_tokens=500,
        repo_owner="acme",
        repo_name="widgets",
        org="acme-org",
    )
    await token_economics.record_call(
        review_run_id=uuid.uuid4(),
        agent_name="sec",
        model="m",
        input_tokens=2000,
        output_tokens=1000,
        repo_owner="acme",
        repo_name="gadget",
        org="acme-org",
    )
    result = await token_economics.get_spend_by_repo()
    assert len(result) >= 2
    repo_names = {r["repo"] for r in result}
    assert "acme/widgets" in repo_names
    assert "acme/gadget" in repo_names


@pytest.mark.asyncio
async def test_record_call_raises_when_not_connected():
    import uuid

    svc = TokenEconomicsService(db_path=":memory:")
    # Do NOT call connect()
    with pytest.raises(RuntimeError, match="not connected"):
        await svc.record_call(
            review_run_id=uuid.uuid4(),
            agent_name="sec",
            model="m",
            input_tokens=1000,
            output_tokens=500,
            repo_owner="acme",
            repo_name="w",
        )


@pytest.mark.asyncio
async def test_get_spend_raises_when_not_connected():
    svc = TokenEconomicsService(db_path=":memory:")
    with pytest.raises(RuntimeError, match="not connected"):
        await svc.get_spend(repo_owner="acme")


@pytest.mark.asyncio
async def test_get_spend_by_org_raises_when_not_connected():
    svc = TokenEconomicsService(db_path=":memory:")
    with pytest.raises(RuntimeError, match="not connected"):
        await svc.get_spend_by_org()


@pytest.mark.asyncio
async def test_get_spend_by_repo_raises_when_not_connected():
    svc = TokenEconomicsService(db_path=":memory:")
    with pytest.raises(RuntimeError, match="not connected"):
        await svc.get_spend_by_repo()
