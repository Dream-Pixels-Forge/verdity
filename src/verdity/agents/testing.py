"""
Testing Specialist Agent.

Reviews PR diffs for test coverage gaps and testing best practices.
"""

from __future__ import annotations

import logging

from verdity.agents.base import BaseSpecialistAgent
from verdity.schemas import (
    ConcernType,
    EvidenceItem,
    Finding,
    SpecialistContext,
    Severity,
)
from verdity.semantic_index import SemanticIndex

logger = logging.getLogger(__name__)

TEST_PATTERNS: list[tuple[str, str, str, str]] = [
    (
        "test_function_added",
        "def test_",
        "info",
        "Test function added — verify implementation coverage",
    ),
    ("untested_branch", "    pass", "medium", "Empty branch (pass) may indicate untested path"),
    ("mock_usage", "mock.patch", "info", "Mock usage detected — verify mock scope is appropriate"),
    (
        "assert_no_message",
        "assert ",
        "low",
        "Assertion found — verify test covers behavior not messages",
    ),
]


class TestingAgent(BaseSpecialistAgent):
    """Testing specialist agent for Verdity."""

    AGENT_VERSION = "testing-agent@0.1.0"
    SPECIALIST_NAME = "testing"
    CONCERN_TYPE = ConcernType.TESTING
    _input_tokens_per_finding = 200
    _output_tokens_per_finding = 30

    async def _scan(
        self,
        ctx: SpecialistContext,
        semantic_index: SemanticIndex,
    ) -> list[Finding]:
        findings: list[Finding] = []

        for file_info in ctx.diff_files:
            path = file_info.get("path", "")
            additions = file_info.get("additions", "")
            if not additions:
                continue

            for name, pattern, severity, explanation in TEST_PATTERNS:
                if pattern.lower() in additions.lower():
                    lines = additions.split("\n")
                    for i, line in enumerate(lines, start=1):
                        if pattern.lower() in line.lower():
                            findings.append(
                                Finding(
                                    concern=ConcernType.TESTING,
                                    severity=Severity(severity),
                                    file=path,
                                    line_start=i,
                                    line_end=i,
                                    summary=f"{name.replace('_', ' ').title()}",
                                    explanation=f"{explanation} at {path}:{i}",
                                    suggested_fix_diff=None,
                                    confidence=0.5 if severity == "info" else 0.65,
                                    evidence=[
                                        EvidenceItem(
                                            tool="test_analyzer", query=pattern, result=name
                                        )
                                    ],
                                    agent_version=self.AGENT_VERSION,
                                    prompt_hash=self._prompt_hash(name, path, str(i)),
                                )
                            )
                            break

        return findings
