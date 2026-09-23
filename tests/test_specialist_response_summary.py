"""SpecialistResponse.summary derivation — issue #8.

mcp_server reads `result.summary` at every review use site; the model must
always provide a real, sensible summary (never AttributeError, never silent "").
"""

from __future__ import annotations

import uuid

from verdity.schemas import ConcernType, Finding, Severity, SpecialistResponse


def _finding() -> Finding:
    return Finding(
        concern=ConcernType.SECURITY,
        severity=Severity.HIGH,
        file="x.py",
        line_start=1,
        line_end=1,
        summary="Potential HARDcoded password detected",
        explanation="e",
        confidence=0.5,
        agent_version="v",
        prompt_hash="h",
    )


def _response(**kwargs) -> SpecialistResponse:
    return SpecialistResponse(
        review_run_id=uuid.uuid4(),
        specialist="security",
        status="complete",
        **kwargs,
    )


class TestSummaryDerivation:
    def test_empty_findings_derives_no_findings(self):
        assert _response().summary == "No findings"

    def test_single_finding_derives_singular_count(self):
        resp = _response(findings=[_finding()])
        assert resp.summary == "1 finding"

    def test_multiple_findings_derives_plural_count(self):
        resp = _response(findings=[_finding(), _finding()])
        assert resp.summary == "2 findings"

    def test_explicit_summary_is_preserved(self):
        resp = _response(summary="Reviewed app.py: 1 blocking issue")
        assert resp.summary == "Reviewed app.py: 1 blocking issue"
