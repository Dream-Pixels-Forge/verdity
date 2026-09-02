"""
Tests for the Metrics Store (Phase 9).
"""

from __future__ import annotations

import uuid

import pytest

from verdity.metrics_store import MetricsStore


@pytest.fixture
async def store():
    """Create an in-memory MetricsStore for testing."""
    s = MetricsStore(db_path=":memory:")
    await s.connect()
    yield s
    await s.close()


class TestMetricsStoreConnect:
    @pytest.mark.asyncio
    async def test_connect_creates_tables(self, store):
        """Tables should exist after connect()."""
        assert store._conn is not None

    @pytest.mark.asyncio
    async def test_close_disconnects(self):
        """close() should disconnect cleanly."""
        s = MetricsStore(db_path=":memory:")
        await s.connect()
        await s.close()
        assert s._conn is None

    @pytest.mark.asyncio
    async def test_operations_before_connect_raise(self):
        """Operations before connect() should raise RuntimeError."""
        s = MetricsStore(db_path=":memory:")
        with pytest.raises(RuntimeError, match="not connected"):
            await s.record_review_metrics(repo_id="r", pr_number=1, metrics={"a": 1.0})


class TestRecordReviewMetrics:
    @pytest.mark.asyncio
    async def test_record_single_metric(self, store):
        """A single metric should be persisted."""
        await store.record_review_metrics(
            repo_id="acme/widgets",
            pr_number=42,
            metrics={"finding_count": 5.0},
        )
        summary = await store.get_repo_summary("acme/widgets")
        assert summary["review_count"] == 1
        assert summary["total_findings"] == 5.0

    @pytest.mark.asyncio
    async def test_record_multiple_metrics(self, store):
        """Multiple metrics in one call should all be recorded."""
        await store.record_review_metrics(
            repo_id="acme/widgets",
            pr_number=42,
            metrics={
                "finding_count": 3.0,
                "severity_critical": 1.0,
                "severity_high": 2.0,
                "cost_usd": 0.15,
            },
        )
        summary = await store.get_repo_summary("acme/widgets")
        assert summary["total_findings"] == 3.0
        assert summary["severity_distribution"].get("critical") == 1.0
        assert summary["severity_distribution"].get("high") == 2.0
        assert summary["total_cost_usd"] == 0.15

    @pytest.mark.asyncio
    async def test_append_only_no_updates(self, store):
        """Constraint #14: no UPDATE or DELETE on review_metrics."""
        await store.record_review_metrics(
            repo_id="acme/widgets",
            pr_number=1,
            metrics={"finding_count": 1.0},
        )
        await store.record_review_metrics(
            repo_id="acme/widgets",
            pr_number=1,
            metrics={"finding_count": 2.0},
        )
        summary = await store.get_repo_summary("acme/widgets")
        # Both records exist — append-only means sum is 3.0, not overwritten
        assert summary["total_findings"] == 3.0


class TestRecordFindingOutcome:
    @pytest.mark.asyncio
    async def test_record_confirmed_outcome(self, store):
        """A confirmed outcome should be recorded."""
        fid = str(uuid.uuid4())
        await store.record_finding_outcome(
            finding_id=fid,
            repo_id="acme/widgets",
            pr_number=10,
            final_outcome="confirmed",
            confidence=0.95,
            severity="high",
            concern="security",
        )
        summary = await store.get_repo_summary("acme/widgets")
        assert summary["outcome_counts"].get("confirmed") == 1
        assert summary["false_positive_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_record_false_positive_outcome(self, store):
        """A false_positive outcome should affect false_positive_rate."""
        fid = str(uuid.uuid4())
        await store.record_finding_outcome(
            finding_id=fid,
            repo_id="acme/widgets",
            final_outcome="false_positive",
        )
        summary = await store.get_repo_summary("acme/widgets")
        assert summary["outcome_counts"].get("false_positive") == 1
        assert summary["false_positive_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_invalid_outcome_raises(self, store):
        """Invalid outcome values should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid outcome"):
            await store.record_finding_outcome(
                finding_id="f1",
                repo_id="r",
                final_outcome="invalid_outcome",
            )

    @pytest.mark.asyncio
    async def test_false_positive_rate_calculation(self, store):
        """False positive rate should be fp_count / total_outcomes."""
        # 3 confirmed, 1 false positive → fp_rate = 0.25
        for i in range(3):
            await store.record_finding_outcome(
                finding_id=f"fp-{i}",
                repo_id="acme/w",
                final_outcome="confirmed",
            )
        await store.record_finding_outcome(
            finding_id="fp-fp",
            repo_id="acme/w",
            final_outcome="false_positive",
        )
        summary = await store.get_repo_summary("acme/w")
        assert summary["false_positive_rate"] == 0.25


class TestRecordReviewTiming:
    @pytest.mark.asyncio
    async def test_record_timing(self, store):
        """Timing should be recorded and retrievable."""
        await store.record_review_timing(
            repo_id="acme/widgets",
            pr_number=1,
            phase="total",
            duration_ms=1500.0,
        )
        summary = await store.get_repo_summary("acme/widgets")
        assert summary["median_time_to_review"] == 1500.0

    @pytest.mark.asyncio
    async def test_median_timing_multiple_reviews(self, store):
        """Median should be computed correctly for multiple reviews."""
        for ms in [1000.0, 2000.0, 3000.0]:
            await store.record_review_timing(
                repo_id="acme/w",
                pr_number=1,
                phase="total",
                duration_ms=ms,
            )
        summary = await store.get_repo_summary("acme/w")
        assert summary["median_time_to_review"] == 2000.0


class TestGetRepoSummary:
    @pytest.mark.asyncio
    async def test_empty_repo_returns_zeros(self, store):
        """Empty repo should return zero/default values."""
        summary = await store.get_repo_summary("nonexistent/repo")
        assert summary["review_count"] == 0
        assert summary["total_findings"] == 0.0
        assert summary["false_positive_rate"] == 0.0
        assert summary["median_time_to_review"] is None
        assert summary["total_cost_usd"] == 0.0
        assert summary["cost_per_review"] == 0.0

    @pytest.mark.asyncio
    async def test_repo_id_filtering(self, store):
        """Metrics should be filtered by repo_id."""
        await store.record_review_metrics(
            repo_id="repo-a", pr_number=1, metrics={"finding_count": 5.0}
        )
        await store.record_review_metrics(
            repo_id="repo-b", pr_number=1, metrics={"finding_count": 10.0}
        )
        summary_a = await store.get_repo_summary("repo-a")
        summary_b = await store.get_repo_summary("repo-b")
        assert summary_a["total_findings"] == 5.0
        assert summary_b["total_findings"] == 10.0

    @pytest.mark.asyncio
    async def test_cost_per_review(self, store):
        """cost_per_review should be total_cost / review_count."""
        await store.record_review_metrics(repo_id="r", pr_number=1, metrics={"cost_usd": 0.30})
        await store.record_review_metrics(repo_id="r", pr_number=2, metrics={"cost_usd": 0.20})
        summary = await store.get_repo_summary("r")
        assert summary["total_cost_usd"] == 0.50
        assert summary["cost_per_review"] == 0.25


class TestGetRepoDashboard:
    @pytest.mark.asyncio
    async def test_dashboard_returns_summary_and_daily(self, store):
        """Dashboard should include summary + daily breakdown."""
        await store.record_review_metrics(repo_id="r", pr_number=1, metrics={"finding_count": 3.0})
        dashboard = await store.get_repo_dashboard("r")
        assert "summary" in dashboard
        assert "daily_reviews" in dashboard
        assert "daily_costs" in dashboard
        assert dashboard["summary"]["review_count"] == 1


class TestRouterOutcomes:
    """Test the router's record_routing_outcomes function."""

    @pytest.mark.asyncio
    async def test_record_auto_approve_outcome(self, store):
        """Auto-approved findings should be recorded as auto_fixed."""
        from verdity.router import RouteAction, RoutingDecision, record_routing_outcomes
        from verdity.schemas import ConcernType, Finding, Severity

        finding = Finding(
            concern=ConcernType.SECURITY,
            severity=Severity.HIGH,
            file="src/auth.py",
            line_start=10,
            line_end=10,
            summary="Test finding",
            explanation="Test",
            confidence=0.95,
            agent_version="test@0.1.0",
            prompt_hash="sha256:abc123",
        )
        decision = RoutingDecision(action=RouteAction.AUTO_APPROVE, confidence=0.95, reason="high")

        await record_routing_outcomes(
            store,
            [(finding, decision)],
            repo_id="acme/widgets",
            pr_number=42,
        )
        summary = await store.get_repo_summary("acme/widgets")
        assert summary["outcome_counts"].get("auto_fixed") == 1

    @pytest.mark.asyncio
    async def test_record_auto_dismiss_outcome(self, store):
        """Auto-dismissed findings should be recorded as false_positive."""
        from verdity.router import RouteAction, RoutingDecision, record_routing_outcomes
        from verdity.schemas import ConcernType, Finding, Severity

        finding = Finding(
            concern=ConcernType.CODE_QUALITY,
            severity=Severity.LOW,
            file="src/util.py",
            line_start=1,
            line_end=1,
            summary="Low confidence",
            explanation="Test",
            confidence=0.3,
            agent_version="test@0.1.0",
            prompt_hash="sha256:def456",
        )
        decision = RoutingDecision(action=RouteAction.AUTO_DISMISS, confidence=0.3, reason="low")

        await record_routing_outcomes(
            store,
            [(finding, decision)],
            repo_id="acme/widgets",
        )
        summary = await store.get_repo_summary("acme/widgets")
        assert summary["outcome_counts"].get("false_positive") == 1

    @pytest.mark.asyncio
    async def test_manual_review_not_recorded(self, store):
        """Manual review findings should NOT have an automatic outcome."""
        from verdity.router import RouteAction, RoutingDecision, record_routing_outcomes
        from verdity.schemas import ConcernType, Finding, Severity

        finding = Finding(
            concern=ConcernType.TESTING,
            severity=Severity.MEDIUM,
            file="src/test.py",
            line_start=5,
            line_end=5,
            summary="Medium confidence",
            explanation="Test",
            confidence=0.7,
            agent_version="test@0.1.0",
            prompt_hash="sha256:ghi789",
        )
        decision = RoutingDecision(
            action=RouteAction.MANUAL_REVIEW, confidence=0.7, reason="medium"
        )

        await record_routing_outcomes(
            store,
            [(finding, decision)],
            repo_id="acme/widgets",
        )
        summary = await store.get_repo_summary("acme/widgets")
        assert summary["outcome_counts"] == {}


# ── Gate Test ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gate_phase9_metrics(store):
    """Phase 9 gate: full review cycle writes metrics, get_repo_summary returns valid data."""
    repo_id = "acme/widgets"
    pr_number = 42

    # Simulate a full review cycle:
    # 1. Record review metrics
    await store.record_review_metrics(
        repo_id=repo_id,
        pr_number=pr_number,
        metrics={
            "finding_count": 5.0,
            "severity_critical": 1.0,
            "severity_high": 2.0,
            "severity_medium": 1.0,
            "severity_low": 1.0,
            "cost_usd": 0.25,
        },
    )

    # 2. Record timing
    await store.record_review_timing(
        repo_id=repo_id,
        pr_number=pr_number,
        phase="total",
        duration_ms=4500.0,
    )

    # 3. Record finding outcomes (simulating human decisions)
    for i in range(3):
        await store.record_finding_outcome(
            finding_id=f"finding-{i}",
            repo_id=repo_id,
            pr_number=pr_number,
            final_outcome="confirmed",
            confidence=0.9,
            severity="high",
            concern="security",
        )
    await store.record_finding_outcome(
        finding_id="finding-fp",
        repo_id=repo_id,
        pr_number=pr_number,
        final_outcome="false_positive",
        confidence=0.5,
        severity="medium",
        concern="code_quality",
    )
    await store.record_finding_outcome(
        finding_id="finding-af",
        repo_id=repo_id,
        pr_number=pr_number,
        final_outcome="auto_fixed",
        confidence=0.95,
        severity="critical",
        concern="security",
    )

    # 4. Verify summary
    summary = await store.get_repo_summary(repo_id)

    assert summary["repo_id"] == repo_id
    assert summary["review_count"] == 1
    assert summary["total_findings"] == 5.0
    assert summary["severity_distribution"]["critical"] == 1.0
    assert summary["severity_distribution"]["high"] == 2.0
    assert summary["severity_distribution"]["medium"] == 1.0
    assert summary["severity_distribution"]["low"] == 1.0
    assert summary["false_positive_rate"] == 0.2  # 1 fp / 5 total
    assert summary["median_time_to_review"] == 4500.0
    assert summary["total_cost_usd"] == 0.25
    assert summary["cost_per_review"] == 0.25
    assert summary["outcome_counts"]["confirmed"] == 3
    assert summary["outcome_counts"]["false_positive"] == 1
    assert summary["outcome_counts"]["auto_fixed"] == 1


# ── Branch Coverage Tests ────────────────────────────────────────────


class TestBranchCoverage:
    """Cover remaining branches for 100% coverage."""

    @pytest.mark.asyncio
    async def test_record_outcome_invalid_outcome_raises(self, store):
        """record_finding_outcome raises ValueError for invalid outcome."""
        with pytest.raises(ValueError, match="Invalid outcome"):
            await store.record_finding_outcome(
                finding_id="00000000-0000-0000-0000-000000000001",
                repo_id="r",
                pr_number=1,
                final_outcome="bogus",
                confidence=0.5,
                severity="medium",
                concern="security",
            )

    @pytest.mark.asyncio
    async def test_record_outcome_before_connect_raises(self):
        s = MetricsStore(db_path=":memory:")
        with pytest.raises(RuntimeError, match="not connected"):
            await s.record_finding_outcome(
                finding_id="00000000-0000-0000-0000-000000000001",
                repo_id="r",
                pr_number=1,
                final_outcome="confirmed",
                confidence=0.5,
                severity="medium",
                concern="security",
            )

    @pytest.mark.asyncio
    async def test_record_timing_before_connect_raises(self):
        s = MetricsStore(db_path=":memory:")
        with pytest.raises(RuntimeError, match="not connected"):
            await s.record_review_timing(
                repo_id="r",
                pr_number=1,
                phase="ingestion",
                duration_ms=100,
            )

    @pytest.mark.asyncio
    async def test_get_repo_summary_before_connect_raises(self):
        s = MetricsStore(db_path=":memory:")
        with pytest.raises(RuntimeError, match="not connected"):
            await s.get_repo_summary("r")

    @pytest.mark.asyncio
    async def test_get_repo_summary_with_even_median(self, store):
        """With even count of timings, median is the average of two middles."""
        # Record 4 timings (even count, > 1)
        for ms in [100, 200, 300, 400]:
            await store.record_review_timing(
                repo_id="r", pr_number=1, phase="total", duration_ms=ms
            )
        summary = await store.get_repo_summary("r")
        # median of [100, 200, 300, 400] sorted = (200 + 300) / 2 = 250
        assert summary["median_time_to_review"] == 250.0

    @pytest.mark.asyncio
    async def test_get_repo_summary_with_odd_median(self, store):
        """With odd count, median is the middle element."""
        for ms in [100, 200, 300]:
            await store.record_review_timing(
                repo_id="r", pr_number=1, phase="total", duration_ms=ms
            )
        summary = await store.get_repo_summary("r")
        assert summary["median_time_to_review"] == 200.0

    @pytest.mark.asyncio
    async def test_get_repo_summary_with_single_median(self, store):
        """With 1 timing, median is that timing."""
        await store.record_review_timing(repo_id="r", pr_number=1, phase="total", duration_ms=42)
        summary = await store.get_repo_summary("r")
        assert summary["median_time_to_review"] == 42.0

    @pytest.mark.asyncio
    async def test_get_all_outcomes_before_connect_raises(self):
        s = MetricsStore(db_path=":memory:")
        with pytest.raises(RuntimeError, match="not connected"):
            await s.get_all_outcomes()

    @pytest.mark.asyncio
    async def test_get_all_outcomes_repo_filter(self, store):
        """get_all_outcomes filters by repo_id when provided."""
        await store.record_finding_outcome(
            finding_id="00000000-0000-0000-0000-000000000001",
            repo_id="r1",
            pr_number=1,
            final_outcome="confirmed",
            confidence=0.5,
            severity="high",
            concern="security",
        )
        await store.record_finding_outcome(
            finding_id="00000000-0000-0000-0000-000000000002",
            repo_id="r2",
            pr_number=2,
            final_outcome="false_positive",
            confidence=0.5,
            severity="low",
            concern="code_quality",
        )
        rows = await store.get_all_outcomes(repo_id="r1")
        assert len(rows) == 1
        assert rows[0]["repo_id"] == "r1"

    @pytest.mark.asyncio
    async def test_get_all_outcomes_with_days_filter(self, store):
        """get_all_outcomes applies days filter when provided."""
        await store.record_finding_outcome(
            finding_id="00000000-0000-0000-0000-000000000003",
            repo_id="r",
            pr_number=1,
            final_outcome="confirmed",
            confidence=0.5,
            severity="high",
            concern="security",
        )
        rows = await store.get_all_outcomes(repo_id="r", days=30)
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_get_repo_dashboard_before_connect_raises(self):
        s = MetricsStore(db_path=":memory:")
        with pytest.raises(RuntimeError, match="not connected"):
            await s.get_repo_dashboard("r")

    @pytest.mark.asyncio
    async def test_get_repo_dashboard_success(self, store):
        """get_repo_dashboard returns a dict with summary and daily breakdown."""
        await store.record_review_metrics(
            repo_id="r",
            pr_number=1,
            metrics={
                "total_findings": 3.0,
                "cost_usd": 0.05,
                "tokens_input": 100.0,
                "tokens_output": 50.0,
            },
        )
        await store.record_review_timing(repo_id="r", pr_number=1, phase="total", duration_ms=200)
        dash = await store.get_repo_dashboard("r", days=30)
        assert "summary" in dash
        assert "daily_reviews" in dash
        assert dash["summary"]["review_count"] == 1
