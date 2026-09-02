"""
Adversarial Self-Review Loop — false-positive reduction via challenge.

Phase 11 implements a challenge-based adversarial review that attempts to
DISPROVE each finding before it reaches the routing gate. This is the same
principle that makes SonarQube Hunter Agent achieve 80-90% precision:
every finding must survive adversarial scrutiny.

Key design:
- The adversarial reviewer uses a DIFFERENT system prompt than the initial
  agents (non-negotiable safety property — prevents self-confirmation bias).
- "lite" depth: deterministic heuristic-based challenge (fast, no LLM).
- "full" depth: LLM-enhanced challenge (deeper analysis, Phase 12 ready).
- Runs on ALL findings, not just high-confidence ones (Assumption #12).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum

from verdity.schemas._models import Finding, Severity

logger = logging.getLogger(__name__)


# ── Adversarial Verdicts ──────────────────────────────────────────────


class Verdict(str, Enum):
    """Possible outcomes of an adversarial challenge."""

    CONFIRMED = "confirmed"  # finding survived challenge → keep + boost
    DISPUTED = "disputed"  # finding is questionable → flag for manual review
    OVERTURNED = "overturned"  # finding is false positive → remove


@dataclass
class AdversarialResult:
    """Result of challenging a single finding."""

    finding_id: str
    verdict: Verdict
    reasoning: str
    suggested_confidence_adjustment: float  # +0.1 for confirmed, -0.2 for disputed, -0.5 for overturned
    evidence: list[str] = field(default_factory=list)


@dataclass
class AdversarialReview:
    """Aggregate result of adversarial review across all findings."""

    results: list[AdversarialResult]
    overturned_count: int
    disputed_count: int
    confirmed_count: int
    total_findings: int


# ── Adversarial Challenge Prompt (SYSTEM-2 — different from agent prompts) ─


ADVERSARIAL_SYSTEM_PROMPT_LITE = """\
You are an adversarial reviewer. Your job is to CHALLENGE findings, not confirm them.
For each finding, ask: "Is this REALLY a problem, or is it a false positive?"
Apply these challenge heuristics:
1. Is the finding pointing at dead code, test fixtures, or intentionally unsafe patterns?
2. Is the severity inflated relative to the actual risk?
3. Is the explanation logically consistent with the code context?
4. Would a senior engineer flag this in a real code review?
Be skeptical. Default to overturning weak findings.
"""

ADVERSARIAL_SYSTEM_PROMPT_FULL = """\
You are an adversarial security reviewer with deep expertise in code analysis.
Your PRIMARY goal is to eliminate false positives. For each finding:
1. Verify the finding against actual code behavior (not just pattern matching).
2. Check if the "issue" is actually intentional or acceptable in context.
3. Assess if the severity matches the real-world impact.
4. Consider if the suggested fix would actually improve the code.
5. Look for evidence that contradicts the finding.
Be ruthless about overturning false positives. Precision > recall.
"""


# ── Heuristic Challenge Rules (deterministic, no LLM) ─────────────────


# Patterns that indicate test code, examples, or non-production code
_TEST_PATTERNS = [
    re.compile(r"test[_/]", re.IGNORECASE),
    re.compile(r"spec[_/]", re.IGNORECASE),
    re.compile(r"mock[_/]", re.IGNORECASE),
    re.compile(r"fixture[_/]", re.IGNORECASE),
    re.compile(r"examples?[_/]", re.IGNORECASE),
    re.compile(r"sample[_/]", re.IGNORECASE),
    re.compile(r"\btest_\w+\.py$", re.IGNORECASE),
    re.compile(r"\w+_test\.py$", re.IGNORECASE),
]

# Patterns that indicate intentionally unsafe code (docs, examples, escape hatches)
_INTENTIONAL_PATTERNS = [
    re.compile(r"# noqa", re.IGNORECASE),
    re.compile(r"# type: ignore", re.IGNORECASE),
    re.compile(r"# pylint: disable", re.IGNORECASE),
    re.compile(r"# nosec", re.IGNORECASE),
    re.compile(r"pragma: no cover", re.IGNORECASE),
    re.compile(r"TODO.*security", re.IGNORECASE),
    re.compile(r"HACK.*intentional", re.IGNORECASE),
]

# Severity inflation: info/low findings that claim to be security issues
_LOW_SEVERITY_CLAIMS_SECURITY = {
    Severity.INFO,
    Severity.LOW,
}

# Files that are configuration, not application code
_CONFIG_PATTERNS = [
    re.compile(r"\.json$", re.IGNORECASE),
    re.compile(r"\.ya?ml$", re.IGNORECASE),
    re.compile(r"\.toml$", re.IGNORECASE),
    re.compile(r"\.cfg$", re.IGNORECASE),
    re.compile(r"\.ini$", re.IGNORECASE),
    re.compile(r"\.env", re.IGNORECASE),
]


class AdversarialReviewer:
    """
    Adversarial self-review: challenges every finding to reduce false positives.

    Uses heuristic-based analysis in "lite" mode (deterministic, no LLM).
    "full" mode is ready for LLM integration (Phase 12).
    """

    def __init__(self, depth: str = "lite") -> None:
        self._depth = depth
        self._system_prompt = (
            ADVERSARIAL_SYSTEM_PROMPT_FULL
            if depth == "full"
            else ADVERSARIAL_SYSTEM_PROMPT_LITE
        )

    async def challenge_findings(
        self,
        findings: list[Finding],
        diff_content: str = "",
        file_contents: dict[str, str] | None = None,
    ) -> AdversarialReview:
        """
        Challenge each finding to determine if it's a true or false positive.

        Args:
            findings: All findings from specialist agents.
            diff_content: The PR diff for context.
            file_contents: Optional map of filename → file content for deeper analysis.

        Returns:
            AdversarialReview with verdicts for each finding.
        """
        if not findings:
            return AdversarialReview(
                results=[],
                overturned_count=0,
                disputed_count=0,
                confirmed_count=0,
                total_findings=0,
            )

        results: list[AdversarialResult] = []
        for finding in findings:
            result = self._challenge_single(finding, diff_content, file_contents or {})
            results.append(result)

        overturned = sum(1 for r in results if r.verdict == Verdict.OVERTURNED)
        disputed = sum(1 for r in results if r.verdict == Verdict.DISPUTED)
        confirmed = sum(1 for r in results if r.verdict == Verdict.CONFIRMED)

        logger.info(
            "Adversarial review: %d findings → %d confirmed, %d disputed, %d overturned",
            len(findings),
            confirmed,
            disputed,
            overturned,
        )

        return AdversarialReview(
            results=results,
            overturned_count=overturned,
            disputed_count=disputed,
            confirmed_count=confirmed,
            total_findings=len(findings),
        )

    def _challenge_single(
        self,
        finding: Finding,
        diff_content: str,
        file_contents: dict[str, str],
    ) -> AdversarialResult:
        """Challenge a single finding using heuristic rules."""
        finding_id = str(finding.finding_id)
        challenges_applied: list[str] = []
        overturn_reasons: list[str] = []
        dispute_reasons: list[str] = []

        # Challenge 1: Test/example/mock file — likely not a real issue
        if self._is_test_or_example_file(finding.file):
            challenges_applied.append("test_or_example_file")
            overturn_reasons.append(
                f"Finding targets test/example file '{finding.file}' — "
                "issues here are typically intentional or irrelevant to production."
            )

        # Challenge 2: Intentionally suppressed code
        if self._is_intentionally_suppressed(diff_content, finding):
            challenges_applied.append("intentionally_suppressed")
            overturn_reasons.append(
                "Code has suppression comments (noqa, nosec, type: ignore) — "
                "the issue was already reviewed and accepted."
            )

        # Challenge 3: Severity inflation — overturn if combined with low confidence
        if self._is_severity_inflated(finding):
            if finding.confidence < 0.5:
                # Severely inflated + low confidence → overturn
                challenges_applied.append("severity_inflated")
                overturn_reasons.append(
                    f"Severity '{finding.severity.value}' is inflated for "
                    f"concern type '{finding.concern.value}' AND confidence is "
                    f"very low ({finding.confidence:.2f}) — likely a false positive."
                )
            else:
                challenges_applied.append("severity_inflated")
                dispute_reasons.append(
                    f"Severity '{finding.severity.value}' appears inflated for "
                    f"concern type '{finding.concern.value}' — "
                    "low-severity items should not block PRs."
                )

        # Challenge 4: Config file flagged as security issue
        if self._is_config_file_misidentified(finding):
            challenges_applied.append("config_file_misidentified")
            overturn_reasons.append(
                f"Finding targets config file '{finding.file}' — "
                "configuration patterns are often false positives in security scans."
            )

        # Challenge 5: Very low confidence
        if finding.confidence < 0.3:
            challenges_applied.append("very_low_confidence")
            dispute_reasons.append(
                f"Confidence {finding.confidence:.2f} is below 0.3 — "
                "the agent itself is uncertain about this finding."
            )

        # Challenge 6: Line range suspiciously large
        if (finding.line_end - finding.line_start) > 50:
            challenges_applied.append("suspiciously_large_range")
            dispute_reasons.append(
                f"Line range {finding.line_start}-{finding.line_end} spans "
                f"{finding.line_end - finding.line_start} lines — "
                "findings covering large blocks are often imprecise."
            )

        # Challenge 7: Suggested fix is empty or trivial
        if (not finding.suggested_fix_diff or finding.suggested_fix_diff.strip() in ("", "# fix", "# TODO")) and finding.severity in (Severity.CRITICAL, Severity.HIGH):
                challenges_applied.append("missing_fix_for_high_severity")
                dispute_reasons.append(
                    "High/critical severity finding without a concrete fix — "
                    "suggests the agent is uncertain about the actual issue."
                )

        # Determine verdict
        if overturn_reasons:
            verdict = Verdict.OVERTURNED
            confidence_adj = -0.5
            reasoning = (
                f"Overturned: {'; '.join(overturn_reasons)} "
                f"[challenges: {', '.join(challenges_applied)}]"
            )
        elif dispute_reasons:
            verdict = Verdict.DISPUTED
            confidence_adj = -0.2
            reasoning = (
                f"Disputed: {'; '.join(dispute_reasons)} "
                f"[challenges: {', '.join(challenges_applied)}]"
            )
        else:
            verdict = Verdict.CONFIRMED
            confidence_adj = 0.1
            reasoning = (
                f"Confirmed: finding survived {len(challenges_applied)} challenge(s) — "
                "no false-positive indicators detected."
                if challenges_applied
                else "Confirmed: no challenges applicable — finding appears valid."
            )

        return AdversarialResult(
            finding_id=finding_id,
            verdict=verdict,
            reasoning=reasoning,
            suggested_confidence_adjustment=confidence_adj,
            evidence=challenges_applied,
        )

    def _is_test_or_example_file(self, filepath: str) -> bool:
        """Check if the file is a test, example, or mock."""
        return any(p.search(filepath) for p in _TEST_PATTERNS)

    def _is_intentionally_suppressed(self, diff_content: str, finding: Finding) -> bool:
        """Check if the finding's code has intentional suppression comments."""
        # Look for suppression patterns near the finding's line range
        for line_num in range(finding.line_start, finding.line_end + 1):
            # Search in diff for lines at this line number
            # Diff format: "+10: code" or "-10: code" or "+ 10: code"
            pattern = re.compile(rf"^[+-]\s*{line_num}\s*[:\s]")
            for diff_line in diff_content.splitlines():
                    if pattern.match(diff_line) and any(p.search(diff_line) for p in _INTENTIONAL_PATTERNS):
                              return True
        return False

    def _is_severity_inflated(self, finding: Finding) -> bool:
        """Check if severity appears inflated for the concern type."""
        # Info/low findings claiming to be security issues are often inflated
        return (
            finding.severity in _LOW_SEVERITY_CLAIMS_SECURITY
            and finding.concern.value == "security"
        )

    def _is_config_file_misidentified(self, finding: Finding) -> bool:
        """Check if a config file is being flagged as a security issue."""
        if finding.concern.value != "security":
            return False
        return any(p.search(finding.file) for p in _CONFIG_PATTERNS)


def apply_verdicts(
    findings: list[Finding],
    review: AdversarialReview,
) -> list[Finding]:
    """
    Apply adversarial verdicts to findings.

    - confirmed → keep + boost confidence (capped at 1.0)
    - disputed → keep but flag for manual review (reduce confidence)
    - overturned → remove from findings

    Returns:
        Filtered and adjusted list of findings.
    """
    # Build lookup from finding_id to verdict
    verdict_map = {r.finding_id: r for r in review.results}

    kept: list[Finding] = []
    for finding in findings:
        result = verdict_map.get(str(finding.finding_id))
        if result is None:
            # Not reviewed — keep as-is
            kept.append(finding)
            continue

        if result.verdict == Verdict.OVERTURNED:
            logger.info(
                "Finding %s overturned and removed: %s",
                finding.finding_id,
                result.reasoning[:100],
            )
            continue

        if result.verdict == Verdict.DISPUTED:
            # Keep but reduce confidence
            new_confidence = max(0.0, finding.confidence + result.suggested_confidence_adjustment)
            finding.confidence = round(new_confidence, 2)
            logger.info(
                "Finding %s disputed — confidence adjusted to %.2f",
                finding.finding_id,
                new_confidence,
            )

        if result.verdict == Verdict.CONFIRMED:
            # Keep and boost confidence
            new_confidence = min(1.0, finding.confidence + result.suggested_confidence_adjustment)
            finding.confidence = round(new_confidence, 2)
            logger.debug(
                "Finding %s confirmed — confidence adjusted to %.2f",
                finding.finding_id,
                new_confidence,
            )

        kept.append(finding)

    return kept
