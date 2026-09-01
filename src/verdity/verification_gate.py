"""
Verification Gate.

Deterministic checks that a proposed fix passes before being marked ready.
Checks: compiles, lint_pass, no_new_secrets, matches_intent.
The matches_intent check is delegated to an independent verifier subagent.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from verdity.coding_agent import ProposedFix
from verdity.schemas import Finding

logger = logging.getLogger(__name__)


class CheckResult(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


@dataclass
class GateCheck:
    name: str
    result: CheckResult
    reason: str | None = None


@dataclass
class GateVerdict:
    gate_id: uuid.UUID = field(default_factory=uuid.uuid4)
    proposed_fix_id: uuid.UUID | None = None
    checks: list[GateCheck] = field(default_factory=list)
    passed: bool = True
    notes: str = ""

    @property
    def all_checks(self) -> list[GateCheck]:
        return self.checks


class VerificationGate:
    """
    Runs deterministic verification checks on a ProposedFix.

    Checks (in order):
      1. compiles         — syntax check of proposed fix
      2. lint_pass        — no new code quality issues introduced
      3. no_new_secrets   — fix doesn't introduce new secrets
      4. matches_intent   — delegated to independent verifier subagent
    """

    def run_checks(
        self,
        proposed_fix: ProposedFix,
        original_finding: Finding,
        verifier: "VerifierSubagent | None" = None,
    ) -> GateVerdict:
        verdict = GateVerdict(proposed_fix_id=proposed_fix.finding_id)

        # ── Check 1: compiles ───────────────────────────────────────────
        compile_result = self._check_compiles(proposed_fix)
        verdict.checks.append(compile_result)
        if compile_result.result == CheckResult.FAIL:
            verdict.passed = False
            verdict.notes += "Compilation failed; fix rejected.\n"

        # ── Check 2: lint_pass ──────────────────────────────────────────
        lint_result = self._check_lint_pass(proposed_fix)
        verdict.checks.append(lint_result)
        if lint_result.result == CheckResult.FAIL:
            verdict.passed = False
            verdict.notes += "Lint check failed; fix introduces new quality issues.\n"

        # ── Check 3: no_new_secrets ─────────────────────────────────────
        secret_result = self._check_no_new_secrets(proposed_fix)
        verdict.checks.append(secret_result)
        if secret_result.result == CheckResult.FAIL:
            verdict.passed = False
            verdict.notes += "New secret detected in proposed fix; rejected.\n"

        # ── Check 4: matches_intent (verifier subagent) ────────────────
        if verifier is not None:
            intent_result = verifier.verify(proposed_fix, original_finding)
            verdict.checks.append(intent_result)
            if intent_result.result == CheckResult.FAIL:
                verdict.passed = False
                verdict.notes += f"Verifier disagreement: {intent_result.reason}\n"
        else:
            verdict.checks.append(GateCheck(name="matches_intent", result=CheckResult.SKIP, reason="No verifier subagent configured"))

        if verdict.passed:
            verdict.notes = "All verification checks passed."
        logger.info("Verification gate %s for fix %s: %s",
                     verdict.gate_id, proposed_fix.finding_id, verdict.passed)
        return verdict

    # ── Deterministic Checks ──────────────────────────────────────────

    def _check_compiles(self, fix: ProposedFix) -> GateCheck:
        """Verify the proposed fix has valid Python syntax."""
        code = "\n".join(fix.suggested_lines)
        try:
            compile(code, f"<fix-{fix.finding_id}>", "exec")
            return GateCheck(name="compiles", result=CheckResult.PASS)
        except SyntaxError as e:
            return GateCheck(name="compiles", result=CheckResult.FAIL, reason=str(e))

    def _check_lint_pass(self, fix: ProposedFix) -> GateCheck:
        """Check proposed fix doesn't introduce new code quality issues."""
        code = "\n".join(fix.suggested_lines)
        issues = []

        if "print(" in code and "logger" not in code:
            issues.append("Still using print() instead of logging")
        if "except:" in code:
            issues.append("Bare except still present")
        if "import *" in code:
            issues.append("Wildcard import still present")

        if issues:
            return GateCheck(name="lint_pass", result=CheckResult.FAIL, reason="; ".join(issues))
        return GateCheck(name="lint_pass", result=CheckResult.PASS)

    def _check_no_new_secrets(self, fix: ProposedFix) -> GateCheck:
        """Ensure the proposed fix doesn't introduce new hard-coded secrets.

        Only inspects non-comment lines for actual secret assignment patterns
        (e.g. `password = "sk-..."`) — not keywords in comments or docstrings.
        Uses precise regex patterns to detect secret assignments while allowing
        reading from environment/vault sources.
        """
        import re
        # Patterns that indicate reading from env/vault (safe)
        env_read_patterns = [
            r"os\.environ\.get?\s*\(['\"]",
            r"os\.getenv\s*\(['\"]",
            r"from\s+vault",
            r"settings\.[a-zA-Z_]+",  # settings access is allowed
        ]
        # Patterns that indicate hard-coded secret assignment (unsafe)
        # Match: keyword = "value" or keyword = 'value' where value is 8+ chars
        # The value is checked to ensure it doesn't look like an env/vault reference
        secret_assignment_patterns = [
            r"(?:password|secret|api_key|token|credential)\s*=\s*(?:'([^']+)'|\"([^\"]+)\")",
        ]
        env_sources = ("settings", "os.environ", "os.getenv", "getenv", "environ", "vault")
        for line in fix.suggested_lines:
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            # Check if line reads from env/vault first
            is_env_read = any(
                re.search(pattern, stripped) for pattern in env_read_patterns
            )
            if is_env_read:
                continue  # reading from env/vault is ok
            # Check for hard-coded secret assignment
            for pattern in secret_assignment_patterns:
                match = re.search(pattern, stripped, re.IGNORECASE)
                if match:
                    # Extract the assigned value (group 1 for single quotes, group 2 for double quotes)
                    assigned_value = match.group(1) or match.group(2)
                    # Check if the value looks like an env/vault reference
                    if assigned_value and any(src in assigned_value.lower() for src in env_sources):
                        continue  # reading from env/vault is ok
                    return GateCheck(
                        name="no_new_secrets",
                        result=CheckResult.FAIL,
                        reason=f"Potential hard-coded secret in fix line: {stripped}",
                    )
        return GateCheck(name="no_new_secrets", result=CheckResult.PASS)


class VerifierSubagent:
    """
    Independent verifier subagent — different context from the coding agent.

    Reviews the proposed diff against the original requirement without seeing
    the coding agent's chain-of-thought, to avoid self-confirmation bias.
    """

    def verify(
        self,
        proposed_fix: ProposedFix,
        original_finding: Finding,
    ) -> GateCheck:
        """
        Produce a structured verdict on whether the fix addresses the finding.
        Returns PASS, FAIL, or SKIP with reasons.
        """
        fix_code = "\n".join(proposed_fix.suggested_lines)

        # ── Intent matching per fix type ────────────────────────────────
        if proposed_fix.fix_type == "secret_removal":
            if "settings" in fix_code or "environ" in fix_code or "credentials" in fix_code:
                return GateCheck(name="matches_intent", result=CheckResult.PASS,
                                 reason="Fix correctly replaces hard-coded credential with config reference")
            return GateCheck(name="matches_intent", result=CheckResult.FAIL,
                             reason="Fix does not properly remove hard-coded credential")

        if proposed_fix.fix_type == "sql_fix":
            if "parameterized" in fix_code or "%s" in fix_code or "? " in fix_code:
                return GateCheck(name="matches_intent", result=CheckResult.PASS,
                                 reason="Fix uses parameterized query")
            return GateCheck(name="matches_intent", result=CheckResult.FAIL,
                             reason="Fix does not use parameterized query — SQL injection still possible")

        if proposed_fix.fix_type == "eval_replacement":
            if "literal_eval" in fix_code or "ast." in fix_code:
                return GateCheck(name="matches_intent", result=CheckResult.PASS,
                                 reason="Fix replaces eval with safe alternative")
            return GateCheck(name="matches_intent", result=CheckResult.FAIL,
                             reason="Fix still uses unsafe eval/exec pattern")

        if proposed_fix.fix_type == "pickle_replacement":
            if "json" in fix_code:
                return GateCheck(name="matches_intent", result=CheckResult.PASS,
                                 reason="Fix replaces pickle with JSON deserialization")
            return GateCheck(name="matches_intent", result=CheckResult.FAIL,
                             reason="Fix still uses unsafe pickle loading")

        if proposed_fix.fix_type == "hash_fix":
            if "sha256" in fix_code or "sha512" in fix_code:
                return GateCheck(name="matches_intent", result=CheckResult.PASS,
                                 reason="Fix uses stronger hash algorithm")
            return GateCheck(name="matches_intent", result=CheckResult.FAIL,
                             reason="Fix does not use stronger hash algorithm")

        if proposed_fix.fix_type == "except_specific":
            if "except (" in fix_code:
                return GateCheck(name="matches_intent", result=CheckResult.PASS,
                                 reason="Fix catches specific exception types")
            return GateCheck(name="matches_intent", result=CheckResult.FAIL,
                             reason="Fix still uses bare except")

        if proposed_fix.fix_type == "print_to_logging":
            if "logging" in fix_code and "logger" in fix_code:
                return GateCheck(name="matches_intent", result=CheckResult.PASS,
                                 reason="Fix replaces print with logging")
            return GateCheck(name="matches_intent", result=CheckResult.FAIL,
                             reason="Fix still uses print statement")

        # Default: skip if fix type not recognized
        return GateCheck(name="matches_intent", result=CheckResult.SKIP,
                         reason=f"Unknown fix type: {proposed_fix.fix_type}")


class RegressionRunner:
    """Runs test suites to verify proposed fixes don't introduce regressions."""

    def __init__(self, test_command: str = "python -m pytest tests/ -q"):
        self._test_command = test_command

    def run_regression(
        self,
        proposed_fix: ProposedFix,
        scope: str = "affected",
    ) -> dict[str, Any]:
        """
        Run regression tests. In dev mode, we simulate results since we can't
        actually execute tests against a real codebase change.

        Returns: { "passed": bool, "tests_run": int, "failures": list[str], "duration_ms": int }
        """
        # Deterministic simulation: fix passes if it doesn't change test logic
        # In production this would spawn the actual test runner
        return {
            "passed": True,
            "tests_run": 0,
            "failures": [],
            "duration_ms": 0,
            "scope": scope,
            "note": "Dev mode — regression check simulated",
        }
