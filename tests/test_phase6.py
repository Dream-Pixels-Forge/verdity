"""
Tests for Phase 6: Coding Agent, Verification Gate, Verifier Subagent, Regression Runner.
"""

from __future__ import annotations


from verdity.coding_agent import CodingAgent, ProposedFix
from verdity.schemas import ConcernType, Finding, Severity
from verdity.verification_gate import (
    CheckResult,
    RegressionRunner,
    VerifierSubagent,
    VerificationGate,
)


def _make_finding(
    concern: ConcernType = ConcernType.SECURITY,
    severity: Severity = Severity.HIGH,
    summary: str = "Hard-coded password detected",
    explanation: str = "password found in source",
    file: str = "src/auth.py",
    line: int = 10,
) -> Finding:
    return Finding(
        concern=concern,
        severity=severity,
        file=file,
        line_start=line,
        line_end=line,
        summary=summary,
        explanation=explanation,
        confidence=0.85,
        evidence=[],
        agent_version="test@0.0.0",
        prompt_hash="abc",
    )


class TestCodingAgent:
    def test_proposes_fix_for_secret_finding(self):
        agent = CodingAgent()
        finding = _make_finding(summary="Hard-coded password detected")
        fix = agent.propose_fix(finding)
        assert fix is not None
        assert fix.fix_type == "secret_removal"
        assert "os.environ.get" in "\n".join(fix.suggested_lines)

    def test_proposes_fix_for_sql_injection(self):
        agent = CodingAgent()
        finding = _make_finding(
            summary="SQL injection via f-string",
            explanation='f"SELECT * FROM users WHERE id = {user_id}"',
        )
        fix = agent.propose_fix(finding)
        assert fix is not None
        assert fix.fix_type == "sql_fix"

    def test_proposes_fix_for_bare_except(self):
        agent = CodingAgent()
        finding = _make_finding(
            concern=ConcernType.CODE_QUALITY,
            summary="Bare except catches all exceptions",
        )
        fix = agent.propose_fix(finding)
        assert fix is not None
        assert fix.fix_type == "except_specific"

    def test_no_fix_for_unsupported_finding(self):
        agent = CodingAgent()
        finding = _make_finding(
            summary="Missing docstring",
            concern=ConcernType.DOCUMENTATION,
        )
        fix = agent.propose_fix(finding)
        assert fix is None

    def test_fix_has_valid_syntax(self):
        agent = CodingAgent()
        finding = _make_finding(summary="Hard-coded password detected")
        fix = agent.propose_fix(finding)
        assert fix is not None
        code = "\n".join(fix.suggested_lines)
        compile(code, "<test>", "exec")  # will raise if invalid syntax


class TestVerifierSubagent:
    def test_passes_secret_removal_fix(self):
        verifier = VerifierSubagent()
        finding = _make_finding(summary="Hard-coded password")
        fix = ProposedFix(
            finding_id=finding.finding_id,
            file=finding.file,
            original_line=finding.line_start,
            suggested_lines=["from verdity.config import settings", "cred = settings.CRED"],
            explanation="use config",
            fix_type="secret_removal",
        )
        result = verifier.verify(fix, finding)
        assert result.result == CheckResult.PASS

    def test_fails_secret_fix_without_config_ref(self):
        verifier = VerifierSubagent()
        finding = _make_finding(summary="Hard-coded password")
        fix = ProposedFix(
            finding_id=finding.finding_id,
            file=finding.file,
            original_line=finding.line_start,
            suggested_lines=["password = 'still_hardcoded'"],
            explanation="wrong fix",
            fix_type="secret_removal",
        )
        result = verifier.verify(fix, finding)
        assert result.result == CheckResult.FAIL

    def test_passes_sql_fix_with_parameterized_query(self):
        verifier = VerifierSubagent()
        finding = _make_finding(summary="SQL injection")
        fix = ProposedFix(
            finding_id=finding.finding_id,
            file=finding.file,
            original_line=finding.line_start,
            suggested_lines=["cursor.execute('SELECT * FROM t WHERE id = %s', (uid,))"],
            explanation="parametrized",
            fix_type="sql_fix",
        )
        result = verifier.verify(fix, finding)
        assert result.result == CheckResult.PASS

    def test_fails_sql_fix_without_parameterization(self):
        verifier = VerifierSubagent()
        finding = _make_finding(summary="SQL injection")
        fix = ProposedFix(
            finding_id=finding.finding_id,
            file=finding.file,
            original_line=finding.line_start,
            suggested_lines=["query = f'SELECT * FROM t WHERE id = {uid}'"],
            explanation="still f-string",
            fix_type="sql_fix",
        )
        result = verifier.verify(fix, finding)
        assert result.result == CheckResult.FAIL


class TestVerificationGate:
    def test_all_checks_pass(self):
        gate = VerificationGate()
        finding = _make_finding(summary="Hard-coded password detected")
        agent = CodingAgent()
        fix = agent.propose_fix(finding)
        assert fix is not None
        verifier = VerifierSubagent()
        verdict = gate.run_checks(fix, finding, verifier=verifier)
        assert verdict.passed
        assert all(c.result != CheckResult.FAIL for c in verdict.checks)

    def test_fix_that_fails_compiles(self):
        gate = VerificationGate()
        finding = _make_finding(summary="Hard-coded password")
        fix = ProposedFix(
            finding_id=finding.finding_id,
            file=finding.file,
            original_line=finding.line_start,
            suggested_lines=["def broken(", "    pass  # syntax error - missing colon"],
            explanation="bad",
            fix_type="secret_removal",
        )
        verifier = VerifierSubagent()
        verdict = gate.run_checks(fix, finding, verifier=verifier)
        assert not verdict.passed
        compile_check = next(c for c in verdict.checks if c.name == "compiles")
        assert compile_check.result == CheckResult.FAIL

    def test_fix_with_new_secret_fails(self):
        gate = VerificationGate()
        finding = _make_finding(summary="SQL injection")
        fix = ProposedFix(
            finding_id=finding.finding_id,
            file=finding.file,
            original_line=finding.line_start,
            suggested_lines=["password = 'leaked_secret_123'", "run_query()"],
            explanation="bad",
            fix_type="sql_fix",
        )
        verdict = gate.run_checks(fix, finding)
        assert not verdict.passed
        secret_check = next(c for c in verdict.checks if c.name == "no_new_secrets")
        assert secret_check.result == CheckResult.FAIL

    def test_verifier_not_configured_skips_intent_check(self):
        gate = VerificationGate()
        finding = _make_finding(summary="Hard-coded password")
        agent = CodingAgent()
        fix = agent.propose_fix(finding)
        assert fix is not None
        verdict = gate.run_checks(fix, finding, verifier=None)
        intent_check = next(c for c in verdict.checks if c.name == "matches_intent")
        assert intent_check.result == CheckResult.SKIP


class TestRegressionRunner:
    def test_runs_regression(self):
        runner = RegressionRunner()
        finding = _make_finding(summary="Test")
        fix = ProposedFix(
            finding_id=finding.finding_id,
            file=finding.file,
            original_line=finding.line_start,
            suggested_lines=["pass"],
            explanation="noop",
            fix_type="secret_removal",
        )
        result = runner.run_regression(fix, scope="affected")
        assert isinstance(result, dict)
        assert "passed" in result
        assert "tests_run" in result
