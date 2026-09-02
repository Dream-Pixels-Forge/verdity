"""
Tests for Phase 11 — Adversarial Self-Review Loop.

Verifies that the adversarial reviewer correctly identifies and overturns
false-positive findings while preserving true positives.
"""

from __future__ import annotations

import uuid

import pytest

from verdity.adversarial_reviewer import (
    ADVERSARIAL_SYSTEM_PROMPT_FULL,
    ADVERSARIAL_SYSTEM_PROMPT_LITE,
    AdversarialResult,
    AdversarialReview,
    AdversarialReviewer,
    Verdict,
    apply_verdicts,
)
from verdity.schemas._models import (
    ConcernType,
    EvidenceItem,
    Finding,
    ReviewPolicy,
    Severity,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _make_finding(
    *,
    concern: ConcernType = ConcernType.SECURITY,
    severity: Severity = Severity.MEDIUM,
    file: str = "src/app.py",
    line_start: int = 10,
    line_end: int = 15,
    confidence: float = 0.7,
    summary: str = "Potential issue",
    explanation: str = "This looks problematic.",
    suggested_fix_diff: str | None = "fix it",
    finding_id: str | None = None,
) -> Finding:
    """Create a test Finding with sensible defaults."""
    return Finding(
        finding_id=uuid.UUID(finding_id) if finding_id else uuid.uuid4(),
        concern=concern,
        severity=severity,
        file=file,
        line_start=line_start,
        line_end=line_end,
        summary=summary,
        explanation=explanation,
        suggested_fix_diff=suggested_fix_diff,
        confidence=confidence,
        evidence=[EvidenceItem(tool="test", result="match")],
        agent_version="0.3.0",
        prompt_hash="abc123",
    )


# ── Gate Test ─────────────────────────────────────────────────────────


class TestGatePhase11:
    """
    Gate test: create 10 findings (5 true positive, 5 false positive),
    run adversarial review, verify >3 of the 5 false positives are overturned.
    """

    @pytest.mark.asyncio
    async def test_gate_phase11_adversarial(self):
        """Phase 11 gate: adversarial review overturns >60% of false positives."""
        # 5 TRUE POSITIVE findings — real issues in production code
        true_positives = [
            _make_finding(
                file="src/auth.py",
                severity=Severity.CRITICAL,
                summary="SQL injection in login query",
                explanation="User input is directly interpolated into SQL query without parameterization.",
                confidence=0.95,
                suggested_fix_diff="cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))",
            ),
            _make_finding(
                file="src/crypto.py",
                severity=Severity.HIGH,
                summary="Weak hash algorithm (MD5) for password storage",
                explanation="MD5 is cryptographically broken. Use bcrypt or argon2.",
                confidence=0.92,
                suggested_fix_diff="bcrypt.hashpw(password, bcrypt.gensalt())",
            ),
            _make_finding(
                file="src/api.py",
                severity=Severity.HIGH,
                summary="Missing rate limiting on authentication endpoint",
                explanation="No rate limiting allows brute-force attacks.",
                confidence=0.88,
                suggested_fix_diff="@rate_limit(limit=5, period=60)",
            ),
            _make_finding(
                file="src/config.py",
                severity=Severity.MEDIUM,
                summary="Hardcoded API key in source code",
                explanation="API key is hardcoded in config.py. Use environment variables.",
                confidence=0.85,
                suggested_fix_diff="os.environ.get('API_KEY')",
            ),
            _make_finding(
                file="src/middleware.py",
                severity=Severity.MEDIUM,
                summary="CORS allows all origins",
                explanation="Access-Control-Allow-Origin: * permits any domain.",
                confidence=0.82,
                suggested_fix_diff="Access-Control-Allow-Origin: https://example.com",
            ),
        ]

        # 5 FALSE POSITIVE findings — should be overturned or disputed
        false_positives = [
            _make_finding(
                file="tests/test_auth.py",  # test file
                severity=Severity.HIGH,
                summary="Hardcoded test credentials",
                explanation="Test file contains hardcoded credentials.",
                confidence=0.75,
            ),
            _make_finding(
                file="docs/examples/sample.py",  # example file
                severity=Severity.MEDIUM,
                summary="SQL injection in example code",
                explanation="Example code has SQL injection.",
                confidence=0.70,
            ),
            _make_finding(
                file="src/app.py",
                severity=Severity.INFO,  # severity inflation
                concern=ConcernType.SECURITY,
                summary="Verbose error messages",
                explanation="Error messages reveal internal paths.",
                confidence=0.40,
                suggested_fix_diff="",
            ),
            _make_finding(
                file="config/settings.yaml",  # config file
                severity=Severity.HIGH,
                summary="Insecure configuration detected",
                explanation="YAML config has security issue.",
                confidence=0.65,
            ),
            _make_finding(
                file="src/utils.py",
                severity=Severity.LOW,
                concern=ConcernType.SECURITY,
                summary="Unused import",
                explanation="Import is not used.",
                confidence=0.20,  # very low confidence
            ),
        ]

        all_findings = true_positives + false_positives
        reviewer = AdversarialReviewer(depth="lite")
        review = await reviewer.challenge_findings(findings=all_findings)

        # Verify gate: >3 of 5 false positives must be overturned
        false_positive_ids = {str(fp.finding_id) for fp in false_positives}
        overturned_fps = sum(
            1
            for r in review.results
            if r.finding_id in false_positive_ids and r.verdict == Verdict.OVERTURNED
        )

        assert overturned_fps > 3, (
            f"Gate failed: only {overturned_fps}/5 false positives overturned "
            f"(need >3). Results: {[(r.finding_id, r.verdict) for r in review.results]}"
        )

        # Verify true positives are NOT overturned
        true_positive_ids = {str(tp.finding_id) for tp in true_positives}
        overturned_tps = sum(
            1
            for r in review.results
            if r.finding_id in true_positive_ids and r.verdict == Verdict.OVERTURNED
        )
        assert overturned_tps == 0, (
            f"True positive was incorrectly overturned: "
            f"{overturned_tps}/5 true positives overturned"
        )

        # Verify summary counts
        assert review.total_findings == 10
        assert review.overturned_count >= 3


# ── Unit Tests ────────────────────────────────────────────────────────


class TestAdversarialReviewer:
    @pytest.mark.asyncio
    async def test_empty_findings(self):
        """No findings → empty review."""
        reviewer = AdversarialReviewer()
        review = await reviewer.challenge_findings(findings=[])
        assert review.total_findings == 0
        assert review.results == []

    @pytest.mark.asyncio
    async def test_test_file_overturned(self):
        """Finding in test file is overturned."""
        reviewer = AdversarialReviewer()
        finding = _make_finding(file="tests/test_app.py", severity=Severity.HIGH)
        review = await reviewer.challenge_findings(findings=[finding])
        assert review.results[0].verdict == Verdict.OVERTURNED
        assert "test_or_example_file" in review.results[0].evidence

    @pytest.mark.asyncio
    async def test_example_file_overturned(self):
        """Finding in example file is overturned."""
        reviewer = AdversarialReviewer()
        finding = _make_finding(file="docs/examples/sample.py")
        review = await reviewer.challenge_findings(findings=[finding])
        assert review.results[0].verdict == Verdict.OVERTURNED

    @pytest.mark.asyncio
    async def test_spec_file_overturned(self):
        """Finding in spec file is overturned."""
        reviewer = AdversarialReviewer()
        finding = _make_finding(file="spec/fixtures/mock_data.py")
        review = await reviewer.challenge_findings(findings=[finding])
        assert review.results[0].verdict == Verdict.OVERTURNED

    @pytest.mark.asyncio
    async def test_severity_inflation_disputed(self):
        """Low-severity security finding is disputed."""
        reviewer = AdversarialReviewer()
        finding = _make_finding(
            severity=Severity.LOW,
            concern=ConcernType.SECURITY,
            confidence=0.5,
        )
        review = await reviewer.challenge_findings(findings=[finding])
        assert review.results[0].verdict == Verdict.DISPUTED
        assert "severity_inflated" in review.results[0].evidence

    @pytest.mark.asyncio
    async def test_info_severity_security_disputed(self):
        """Info-severity security finding with low confidence is overturned."""
        reviewer = AdversarialReviewer()
        finding = _make_finding(
            severity=Severity.INFO,
            concern=ConcernType.SECURITY,
            confidence=0.3,
        )
        review = await reviewer.challenge_findings(findings=[finding])
        # Severity inflated + confidence < 0.5 → overturned
        assert review.results[0].verdict == Verdict.OVERTURNED

    @pytest.mark.asyncio
    async def test_config_file_overturned(self):
        """Security finding in config file is overturned."""
        reviewer = AdversarialReviewer()
        finding = _make_finding(
            file="config/settings.yaml",
            concern=ConcernType.SECURITY,
        )
        review = await reviewer.challenge_findings(findings=[finding])
        assert review.results[0].verdict == Verdict.OVERTURNED
        assert "config_file_misidentified" in review.results[0].evidence

    @pytest.mark.asyncio
    async def test_very_low_confidence_disputed(self):
        """Finding with very low confidence is disputed."""
        reviewer = AdversarialReviewer()
        finding = _make_finding(confidence=0.2)
        review = await reviewer.challenge_findings(findings=[finding])
        assert review.results[0].verdict == Verdict.DISPUTED
        assert "very_low_confidence" in review.results[0].evidence

    @pytest.mark.asyncio
    async def test_suspiciously_large_range_disputed(self):
        """Finding spanning >50 lines is disputed."""
        reviewer = AdversarialReviewer()
        finding = _make_finding(line_start=1, line_end=60)
        review = await reviewer.challenge_findings(findings=[finding])
        assert review.results[0].verdict == Verdict.DISPUTED
        assert "suspiciously_large_range" in review.results[0].evidence

    @pytest.mark.asyncio
    async def test_missing_fix_for_high_severity_disputed(self):
        """High-severity finding without fix is disputed."""
        reviewer = AdversarialReviewer()
        finding = _make_finding(
            severity=Severity.CRITICAL,
            suggested_fix_diff="",
        )
        review = await reviewer.challenge_findings(findings=[finding])
        assert review.results[0].verdict == Verdict.DISPUTED
        assert "missing_fix_for_high_severity" in review.results[0].evidence

    @pytest.mark.asyncio
    async def test_valid_finding_confirmed(self):
        """Real issue in production code is confirmed."""
        reviewer = AdversarialReviewer()
        finding = _make_finding(
            file="src/auth.py",
            severity=Severity.CRITICAL,
            confidence=0.95,
            suggested_fix_diff="use parameterized query",
        )
        review = await reviewer.challenge_findings(findings=[finding])
        assert review.results[0].verdict == Verdict.CONFIRMED

    @pytest.mark.asyncio
    async def test_noqa_suppression_overturned(self):
        """Finding with noqa comment is overturned."""
        reviewer = AdversarialReviewer()
        finding = _make_finding(file="src/app.py")
        diff = "+10: # noqa: S101  password = 'test'"
        review = await reviewer.challenge_findings(findings=[finding], diff_content=diff)
        assert review.results[0].verdict == Verdict.OVERTURNED
        assert "intentionally_suppressed" in review.results[0].evidence

    @pytest.mark.asyncio
    async def test_nosec_suppression_overturned(self):
        """Finding with nosec comment is overturned."""
        reviewer = AdversarialReviewer()
        finding = _make_finding(file="src/app.py", line_start=5, line_end=5)
        diff = "+5: hashed = bcrypt.hashpw(pw, bcrypt.gensalt())  # nosec"
        review = await reviewer.challenge_findings(findings=[finding], diff_content=diff)
        assert review.results[0].verdict == Verdict.OVERTURNED

    @pytest.mark.asyncio
    async def test_multiple_challenges_compound(self):
        """Finding with multiple challenges gets overturned (not disputed)."""
        reviewer = AdversarialReviewer()
        finding = _make_finding(
            file="tests/test_app.py",  # challenge 1: test file
            severity=Severity.INFO,
            concern=ConcernType.SECURITY,  # challenge 2: severity inflation
            confidence=0.2,  # challenge 3: very low confidence
        )
        review = await reviewer.challenge_findings(findings=[finding])
        assert review.results[0].verdict == Verdict.OVERTURNED

    @pytest.mark.asyncio
    async def test_review_counts(self):
        """Review counts match actual results."""
        reviewer = AdversarialReviewer()
        findings = [
            _make_finding(file="tests/test_a.py"),  # overturned (test file)
            _make_finding(file="tests/test_b.py"),  # overturned (test file)
            _make_finding(
                file="src/app.py", severity=Severity.LOW, confidence=0.2
            ),  # overturned (severity inflated + low confidence)
            _make_finding(
                file="src/auth.py", severity=Severity.CRITICAL, confidence=0.95
            ),  # confirmed
        ]
        review = await reviewer.challenge_findings(findings=findings)
        assert review.total_findings == 4
        assert review.overturned_count == 3
        assert review.confirmed_count == 1
        assert review.disputed_count == 0


# ── apply_verdicts Tests ──────────────────────────────────────────────


class TestApplyVerdicts:
    def test_overturned_findings_removed(self):
        """Overturned findings are removed from the list."""
        f1 = _make_finding(finding_id="00000000-0000-0000-0000-000000000001")
        f2 = _make_finding(finding_id="00000000-0000-0000-0000-000000000002")
        review = AdversarialReview(
            results=[
                AdversarialResult(
                    finding_id="00000000-0000-0000-0000-000000000001",
                    verdict=Verdict.OVERTURNED,
                    reasoning="false positive",
                    suggested_confidence_adjustment=-0.5,
                ),
                AdversarialResult(
                    finding_id="00000000-0000-0000-0000-000000000002",
                    verdict=Verdict.CONFIRMED,
                    reasoning="valid",
                    suggested_confidence_adjustment=0.1,
                ),
            ],
            overturned_count=1,
            disputed_count=0,
            confirmed_count=1,
            total_findings=2,
        )
        kept = apply_verdicts([f1, f2], review)
        assert len(kept) == 1
        assert kept[0].finding_id == f2.finding_id

    def test_confirmed_findings_boosted(self):
        """Confirmed findings get confidence boosted."""
        f = _make_finding(finding_id="00000000-0000-0000-0000-000000000001", confidence=0.8)
        review = AdversarialReview(
            results=[
                AdversarialResult(
                    finding_id="00000000-0000-0000-0000-000000000001",
                    verdict=Verdict.CONFIRMED,
                    reasoning="valid",
                    suggested_confidence_adjustment=0.1,
                ),
            ],
            overturned_count=0,
            disputed_count=0,
            confirmed_count=1,
            total_findings=1,
        )
        kept = apply_verdicts([f], review)
        assert len(kept) == 1
        assert kept[0].confidence == 0.9  # 0.8 + 0.1

    def test_confirmed_capped_at_1(self):
        """Confirmed confidence capped at 1.0."""
        f = _make_finding(finding_id="00000000-0000-0000-0000-000000000001", confidence=0.95)
        review = AdversarialReview(
            results=[
                AdversarialResult(
                    finding_id="00000000-0000-0000-0000-000000000001",
                    verdict=Verdict.CONFIRMED,
                    reasoning="valid",
                    suggested_confidence_adjustment=0.1,
                ),
            ],
            overturned_count=0,
            disputed_count=0,
            confirmed_count=1,
            total_findings=1,
        )
        kept = apply_verdicts([f], review)
        assert kept[0].confidence == 1.0  # capped

    def test_disputed_findings_reduced(self):
        """Disputed findings get confidence reduced."""
        f = _make_finding(finding_id="00000000-0000-0000-0000-000000000001", confidence=0.7)
        review = AdversarialReview(
            results=[
                AdversarialResult(
                    finding_id="00000000-0000-0000-0000-000000000001",
                    verdict=Verdict.DISPUTED,
                    reasoning="questionable",
                    suggested_confidence_adjustment=-0.2,
                ),
            ],
            overturned_count=0,
            disputed_count=1,
            confirmed_count=0,
            total_findings=1,
        )
        kept = apply_verdicts([f], review)
        assert len(kept) == 1
        assert kept[0].confidence == 0.5  # 0.7 - 0.2

    def test_disputed_floored_at_0(self):
        """Disputed confidence floored at 0.0."""
        f = _make_finding(finding_id="00000000-0000-0000-0000-000000000001", confidence=0.1)
        review = AdversarialReview(
            results=[
                AdversarialResult(
                    finding_id="00000000-0000-0000-0000-000000000001",
                    verdict=Verdict.DISPUTED,
                    reasoning="questionable",
                    suggested_confidence_adjustment=-0.2,
                ),
            ],
            overturned_count=0,
            disputed_count=1,
            confirmed_count=0,
            total_findings=1,
        )
        kept = apply_verdicts([f], review)
        assert kept[0].confidence == 0.0  # floored

    def test_unreviewed_findings_kept(self):
        """Findings not in the review are kept as-is."""
        f = _make_finding(finding_id="00000000-0000-0000-0000-000000000099", confidence=0.6)
        review = AdversarialReview(
            results=[], overturned_count=0, disputed_count=0, confirmed_count=0, total_findings=0
        )
        kept = apply_verdicts([f], review)
        assert len(kept) == 1
        assert kept[0].confidence == 0.6


# ── Policy Integration Tests ──────────────────────────────────────────


class TestPolicyIntegration:
    def test_review_policy_has_adversarial_fields(self):
        """ReviewPolicy includes adversarial review fields."""
        policy = ReviewPolicy()
        assert policy.adversarial_review_enabled is True
        assert policy.adversarial_review_depth == "lite"

    def test_review_policy_adversarial_disabled(self):
        """ReviewPolicy can disable adversarial review."""
        policy = ReviewPolicy(adversarial_review_enabled=False)
        assert policy.adversarial_review_enabled is False

    def test_review_policy_full_depth(self):
        """ReviewPolicy supports full adversarial depth."""
        policy = ReviewPolicy(adversarial_review_depth="full")
        assert policy.adversarial_review_depth == "full"


# ── Prompt Separation Tests ───────────────────────────────────────────


class TestPromptSeparation:
    def test_lite_prompt_is_different_from_agent_prompts(self):
        """Adversarial lite prompt must differ from agent prompts (safety property)."""
        # Agent prompts contain "analyze" or "review" — adversarial contains "challenge"
        assert "challenge" in ADVERSARIAL_SYSTEM_PROMPT_LITE.lower()
        assert (
            "DISPROVE" in ADVERSARIAL_SYSTEM_PROMPT_LITE
            or "challenge" in ADVERSARIAL_SYSTEM_PROMPT_LITE.lower()
        )

    def test_full_prompt_is_different_from_agent_prompts(self):
        """Adversarial full prompt must differ from agent prompts (safety property)."""
        assert "false positive" in ADVERSARIAL_SYSTEM_PROMPT_FULL.lower()

    def test_lite_and_full_prompts_are_different(self):
        """Lite and full adversarial prompts are different."""
        assert ADVERSARIAL_SYSTEM_PROMPT_LITE != ADVERSARIAL_SYSTEM_PROMPT_FULL


# ── Verdict Enum Tests ────────────────────────────────────────────────


class TestVerdictEnum:
    def test_verdict_values(self):
        """Verdict enum has expected values."""
        assert Verdict.CONFIRMED.value == "confirmed"
        assert Verdict.DISPUTED.value == "disputed"
        assert Verdict.OVERTURNED.value == "overturned"

    def test_verdict_is_str_enum(self):
        """Verdict can be used as a string."""
        assert Verdict.CONFIRMED == "confirmed"


# ── Branch coverage ──────────────────────────────────────────────────


class TestConfigFileMisidentifiedBranch:
    """Cover the 'non-security concern' early-out in _is_config_file_misidentified."""

    def test_non_security_concern_returns_false(self):
        """When concern is not security, _is_config_file_misidentified returns False."""
        reviewer = AdversarialReviewer()
        # config file path but non-security concern
        finding = _make_finding(
            concern=ConcernType.CODE_QUALITY,
            file="config.json",
        )
        assert reviewer._is_config_file_misidentified(finding) is False

    def test_security_concern_returns_bool(self):
        """When concern is security, _is_config_file_misidentified runs regex."""
        reviewer = AdversarialReviewer()
        finding = _make_finding(
            concern=ConcernType.SECURITY,
            file="config.yaml",
        )
        result = reviewer._is_config_file_misidentified(finding)
        assert isinstance(result, bool)
