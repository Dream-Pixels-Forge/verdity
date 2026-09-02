"""
Tests for Trust Calibration — Phase 10 of v0.4.0.

Verifies that TrustCalibrator correctly learns from human feedback
and adjusts confidence weights to reduce false positives.
"""

from __future__ import annotations

import uuid

import pytest

from verdity.metrics_store import MetricsStore
from verdity.router import compute_confidence
from verdity.schemas import ConcernType, Finding, Severity
from verdity.trust_calibration import (
    DEFAULT_CONCERN_BOOST as TC_DEFAULT_CONCERN_BOOST,
)
from verdity.trust_calibration import (
    DEFAULT_SEVERITY_WEIGHTS as TC_DEFAULT_SEVERITY_WEIGHTS,
)
from verdity.trust_calibration import (
    TrustCalibrator,
)


def _make_finding(
    severity: Severity = Severity.MEDIUM,
    concern: ConcernType = ConcernType.SECURITY,
    confidence: float = 0.7,
) -> Finding:
    """Create a minimal valid Finding for testing."""
    return Finding(
        concern=concern,
        severity=severity,
        file="src/test.py",
        line_start=1,
        line_end=1,
        summary="test finding",
        explanation="test explanation",
        confidence=confidence,
        agent_version="0.4.0",
        prompt_hash="abc123",
    )


# ── Unit Tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestTrustCalibratorRecord:
    """Test outcome recording."""

    async def test_record_outcome(self, tmp_path):
        """record_outcome delegates to MetricsStore."""
        store = MetricsStore(str(tmp_path / "metrics.db"))
        await store.connect()
        try:
            cal = TrustCalibrator(store)
            await cal.record_outcome(
                finding_id=str(uuid.uuid4()),
                repo_id="owner/repo",
                outcome="confirmed",
                confidence=0.85,
                severity="high",
                concern="security",
            )
            # Verify it was stored
            outcomes = await store.get_all_outcomes()
            assert len(outcomes) == 1
            assert outcomes[0]["final_outcome"] == "confirmed"
        finally:
            await store.close()

    async def test_record_multiple_outcomes(self, tmp_path):
        """Record multiple outcomes for later calibration."""
        store = MetricsStore(str(tmp_path / "metrics.db"))
        await store.connect()
        try:
            cal = TrustCalibrator(store)
            for i in range(10):
                await cal.record_outcome(
                    finding_id=str(uuid.uuid4()),
                    repo_id="owner/repo",
                    outcome="confirmed" if i < 7 else "false_positive",
                    confidence=0.8,
                    severity="medium",
                    concern="code_quality",
                )
            outcomes = await store.get_all_outcomes()
            assert len(outcomes) == 10
        finally:
            await store.close()


@pytest.mark.asyncio
class TestTrustCalibratorRecalibrate:
    """Test recalibration logic."""

    async def test_recalibrate_insufficient_samples(self, tmp_path):
        """Recalibration returns False when below min_samples."""
        store = MetricsStore(str(tmp_path / "metrics.db"))
        await store.connect()
        try:
            cal = TrustCalibrator(store)
            # Record only 5 outcomes (below default min_samples=50)
            for i in range(5):
                await cal.record_outcome(
                    finding_id=str(uuid.uuid4()),
                    repo_id="owner/repo",
                    outcome="confirmed",
                    confidence=0.8,
                    severity="medium",
                    concern="code_quality",
                )
            result = await cal.recalibrate(min_samples=50)
            assert result is False
            weights = cal.get_adjusted_weights()
            assert weights["calibrated"] is False
        finally:
            await store.close()

    async def test_recalibrate_enough_samples(self, tmp_path):
        """Recalibration returns True with enough samples."""
        store = MetricsStore(str(tmp_path / "metrics.db"))
        await store.connect()
        try:
            cal = TrustCalibrator(store)
            # Record 60 outcomes: 40 confirmed, 20 false_positive
            for i in range(60):
                outcome = "confirmed" if i < 40 else "false_positive"
                severity = "info" if i >= 40 else "high"
                await cal.record_outcome(
                    finding_id=str(uuid.uuid4()),
                    repo_id="owner/repo",
                    outcome=outcome,
                    confidence=0.8,
                    severity=severity,
                    concern="code_quality",
                )
            result = await cal.recalibrate(min_samples=50)
            assert result is True
            weights = cal.get_adjusted_weights()
            assert weights["calibrated"] is True
            assert weights["sample_count"] == 60
        finally:
            await store.close()

    async def test_recalibrate_reduces_low_precision_weights(self, tmp_path):
        """Severity with many false positives gets reduced weight."""
        store = MetricsStore(str(tmp_path / "metrics.db"))
        await store.connect()
        try:
            cal = TrustCalibrator(store)
            # INFO severity: 5 confirmed, 25 false_positive → precision = 0.167
            for i in range(30):
                outcome = "confirmed" if i < 5 else "false_positive"
                await cal.record_outcome(
                    finding_id=str(uuid.uuid4()),
                    repo_id="owner/repo",
                    outcome=outcome,
                    confidence=0.8,
                    severity="info",
                    concern="code_quality",
                )
            # HIGH severity: 25 confirmed, 5 false_positive → precision = 0.833
            for i in range(30):
                outcome = "confirmed" if i < 25 else "false_positive"
                await cal.record_outcome(
                    finding_id=str(uuid.uuid4()),
                    repo_id="owner/repo",
                    outcome=outcome,
                    confidence=0.8,
                    severity="high",
                    concern="code_quality",
                )
            await cal.recalibrate(min_samples=50)
            weights = cal.get_adjusted_weights()
            # INFO weight should be reduced from default 0.1
            assert weights["severity_weights"]["info"] < TC_DEFAULT_SEVERITY_WEIGHTS["info"]
            # HIGH weight should stay near default (precision > 0.8)
            assert weights["severity_weights"]["high"] >= TC_DEFAULT_SEVERITY_WEIGHTS["high"] * 0.8
        finally:
            await store.close()

    async def test_recalibrate_preserves_high_precision(self, tmp_path):
        """High-precision groups keep their default weights."""
        store = MetricsStore(str(tmp_path / "metrics.db"))
        await store.connect()
        try:
            cal = TrustCalibrator(store)
            # All confirmed → precision = 1.0
            for i in range(60):
                await cal.record_outcome(
                    finding_id=str(uuid.uuid4()),
                    repo_id="owner/repo",
                    outcome="confirmed",
                    confidence=0.8,
                    severity="critical",
                    concern="security",
                )
            await cal.recalibrate(min_samples=50)
            weights = cal.get_adjusted_weights()
            assert weights["severity_weights"]["critical"] == TC_DEFAULT_SEVERITY_WEIGHTS["critical"]
            assert weights["concern_boost"]["security"] == TC_DEFAULT_CONCERN_BOOST["security"]
        finally:
            await store.close()


@pytest.mark.asyncio
class TestTrustCalibratorStats:
    """Test calibration statistics."""

    async def test_calibration_stats_empty(self, tmp_path):
        """Stats with no outcomes returns zeros."""
        store = MetricsStore(str(tmp_path / "metrics.db"))
        await store.connect()
        try:
            cal = TrustCalibrator(store)
            stats = await cal.get_calibration_stats()
            assert stats["sample_count"] == 0
            assert stats["precision_at_0.9"] == 0.0
            assert stats["recall_at_0.6"] == 0.0
        finally:
            await store.close()

    async def test_calibration_stats_with_data(self, tmp_path):
        """Stats computed correctly from outcome data."""
        store = MetricsStore(str(tmp_path / "metrics.db"))
        await store.connect()
        try:
            cal = TrustCalibrator(store)
            # High confidence confirmed findings
            for i in range(20):
                await cal.record_outcome(
                    finding_id=str(uuid.uuid4()),
                    repo_id="owner/repo",
                    outcome="confirmed",
                    confidence=0.95,
                    severity="high",
                    concern="security",
                )
            # High confidence false positives
            for i in range(5):
                await cal.record_outcome(
                    finding_id=str(uuid.uuid4()),
                    repo_id="owner/repo",
                    outcome="false_positive",
                    confidence=0.92,
                    severity="medium",
                    concern="testing",
                )
            stats = await cal.get_calibration_stats()
            assert stats["sample_count"] == 25
            # precision_at_0.9: 20 confirmed / 25 total = 0.8
            assert stats["precision_at_0.9"] == pytest.approx(0.8, abs=0.01)
            # recall_at_0.6: all 20 confirmed have conf >= 0.6 → 1.0
            assert stats["recall_at_0.6"] == pytest.approx(1.0, abs=0.01)
        finally:
            await store.close()


# ── Router Integration ───────────────────────────────────────────────


class TestRouterWithCalibratedWeights:
    """Test that compute_confidence accepts calibrated weights."""

    def test_default_weights(self):
        """Using default weights gives same result as no params."""
        finding = _make_finding(severity=Severity.HIGH, confidence=0.8)
        default_score = compute_confidence(finding)
        explicit_score = compute_confidence(
            finding,
            severity_weights={"high": 0.8, "critical": 1.0, "medium": 0.5, "low": 0.3, "info": 0.1},
            concern_boost={"security": 0.15, "code_quality": 0.0, "testing": 0.05, "documentation": 0.0},
        )
        assert default_score == explicit_score

    def test_reduced_info_weight(self):
        """Reducing INFO weight lowers confidence for INFO findings."""
        finding = _make_finding(severity=Severity.INFO, confidence=0.5)
        default_score = compute_confidence(finding)
        reduced_score = compute_confidence(
            finding,
            severity_weights={"info": 0.02},
            concern_boost={"security": 0.15, "code_quality": 0.0, "testing": 0.05, "documentation": 0.0},
        )
        assert reduced_score < default_score

    def test_increased_security_boost(self):
        """Increasing SECURITY boost raises confidence for security findings."""
        finding = _make_finding(
            severity=Severity.LOW,
            concern=ConcernType.SECURITY,
            confidence=0.5,
        )
        default_score = compute_confidence(finding)
        # LOW weight 0.3, SECURITY boost 0.15: 0.5*(1-0.3)+0.3+0.15 = 0.35+0.3+0.15 = 0.80
        assert default_score == pytest.approx(0.80, abs=0.01)
        boosted_score = compute_confidence(
            finding,
            severity_weights={"critical": 1.0, "high": 0.8, "medium": 0.5, "low": 0.3, "info": 0.1},
            concern_boost={"security": 0.3, "code_quality": 0.0, "testing": 0.05, "documentation": 0.0},
        )
        # LOW weight 0.3, SECURITY boost 0.3: 0.5*(1-0.3)+0.3+0.3 = 0.35+0.3+0.3 = 0.95
        assert boosted_score == pytest.approx(0.95, abs=0.01)
        assert boosted_score > default_score

    def test_calibrated_weights_dict_passthrough(self):
        """Calibrated weights dict is passed through correctly."""
        finding = _make_finding(severity=Severity.LOW, confidence=0.4)
        custom_weights = {"low": 0.2}
        custom_boost = {"security": 0.1}
        score = compute_confidence(
            finding,
            severity_weights=custom_weights,
            concern_boost=custom_boost,
        )
        # LOW weight 0.2, SECURITY boost 0.1: 0.4*(1-0.2)+0.2+0.1 = 0.32+0.2+0.1 = 0.62
        assert score == pytest.approx(0.62, abs=0.01)


# ── Gate Test ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gate_phase10_trust(tmp_path):
    """Gate test: record 60 outcomes, recalibrate, verify precision@0.9 > 0.8.

    Scenario:
    - 40 confirmed findings (various severity/concern)
    - 20 false_positive findings (mostly INFO/low-confidence)
    - After calibration, INFO severity weight should be reduced
    - precision@0.9 should be > 0.8
    """
    store = MetricsStore(str(tmp_path / "metrics.db"))
    await store.connect()
    try:
        cal = TrustCalibrator(store)

        # Record 40 confirmed findings (high precision across severities)
        for i in range(40):
            sev = ["critical", "high", "medium", "low", "info"][i % 5]
            con = ["security", "code_quality", "testing", "documentation"][i % 4]
            await cal.record_outcome(
                finding_id=str(uuid.uuid4()),
                repo_id="test/repo",
                outcome="confirmed",
                confidence=0.92 if sev in ("critical", "high") else 0.75,
                severity=sev,
                concern=con,
            )

        # Record 20 false_positive findings (mostly INFO/LOW with moderate confidence)
        for i in range(20):
            sev = "info" if i < 12 else "low"
            con = ["testing", "documentation", "code_quality"][i % 3]
            await cal.record_outcome(
                finding_id=str(uuid.uuid4()),
                repo_id="test/repo",
                outcome="false_positive",
                confidence=0.85 if i < 8 else 0.65,
                severity=sev,
                concern=con,
            )

        # Recalibrate
        result = await cal.recalibrate(min_samples=50)
        assert result is True, "Recalibration should succeed with 60 samples"

        # Get stats
        stats = await cal.get_calibration_stats()
        assert stats["sample_count"] == 60

        # Gate criterion: precision@0.9 > 0.8
        assert stats["precision_at_0.9"] > 0.8, (
            f"Gate failed: precision@0.9 = {stats['precision_at_0.9']:.3f}, expected > 0.8"
        )

        # Verify calibrated weights differ from defaults
        weights = cal.get_adjusted_weights()
        assert weights["calibrated"] is True
        # INFO weight should be reduced (many false positives)
        assert weights["severity_weights"]["info"] < TC_DEFAULT_SEVERITY_WEIGHTS["info"]

        # Verify compute_confidence accepts calibrated weights
        finding = _make_finding(severity=Severity.INFO, confidence=0.8)
        score = compute_confidence(
            finding,
            severity_weights=weights["severity_weights"],
            concern_boost=weights["concern_boost"],
        )
        assert 0.0 <= score <= 1.0
    finally:
        await store.close()


# ── Calibration stats branch coverage ──────────────────────────────────


class TestCalibrationStatsBranches:
    """Cover precision_high_conf=0.0 and recall_med_conf=0.0 fallback branches."""

    async def test_stats_with_only_low_confidence_outcomes(self, tmp_path):
        """Outcomes with confidence < 0.9 and final_outcome=confirmed:
        - precision_high_conf falls to 0.0 (line 213)
        - recall_med_conf computes (some have conf >= 0.6)
        """
        store = MetricsStore(str(tmp_path / "metrics.db"))
        await store.connect()
        try:
            cal = TrustCalibrator(store)
            # Add confirmed with confidence < 0.9 (triggers line 213)
            await cal.record_outcome(
                finding_id=str(uuid.uuid4()),
                repo_id="o/r",
                outcome="confirmed",
                confidence=0.5,  # Below 0.9 threshold
                severity="medium",
                concern="security",
            )
            stats = await cal.get_calibration_stats()
            assert stats["precision_at_0.9"] == 0.0
        finally:
            await store.close()

    async def test_stats_with_no_confirmed_outcomes(self, tmp_path):
        """When there are no 'confirmed' outcomes at all, recall_med_conf=0.0 (line 225)."""
        store = MetricsStore(str(tmp_path / "metrics.db"))
        await store.connect()
        try:
            cal = TrustCalibrator(store)
            # Only false_positive outcomes (not confirmed)
            await cal.record_outcome(
                finding_id=str(uuid.uuid4()),
                repo_id="o/r",
                outcome="false_positive",
                confidence=0.95,
                severity="high",
                concern="security",
            )
            stats = await cal.get_calibration_stats()
            assert stats["recall_at_0.6"] == 0.0
        finally:
            await store.close()
