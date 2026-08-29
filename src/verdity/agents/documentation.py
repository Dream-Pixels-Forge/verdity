"""
Documentation Specialist Agent.

Reviews PR diffs for documentation quality and completeness.
"""

from __future__ import annotations

import logging
import re

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

DOC_PATTERNS: list[tuple[str, str, str, str]] = [
    ("missing_docstring", r"re:def \w+\(.*\):\s*\n\s*(?!\"\"\"|\'\'\')", "info", "Function may lack docstring — add docstring for API clarity"),
    ("changelog_entry", "CHANGELOG", "info", "Changelog entry detected — verify format"),
    ("breaking_change", "breaking change", "medium", "Potential breaking change noted — verify migration docs"),
]


class DocumentationAgent(BaseSpecialistAgent):
    """Documentation specialist agent for Verdity."""

    AGENT_VERSION = "docs-agent@0.1.0"
    SPECIALIST_NAME = "documentation"
    CONCERN_TYPE = ConcernType.DOCUMENTATION
    _input_tokens_per_finding = 150
    _output_tokens_per_finding = 20

    async def _scan(
        self,
        ctx: SpecialistContext,
        semantic_index: SemanticIndex,
    ) -> list[Finding]:
        findings: list[Finding] = []

        for file_info in ctx.diff_files:
            path = file_info.get("path", "")
            content = file_info.get("content", "")
            additions = file_info.get("additions", "")
            scan_text = additions if additions else content

            for name, pattern, severity, explanation in DOC_PATTERNS:
                is_regex = pattern.startswith("re:")
                actual_pattern = pattern[3:] if is_regex else pattern

                if is_regex:
                    m = re.search(actual_pattern, scan_text, re.DOTALL)
                    if m:
                        matched_line = scan_text[:m.start()].count("\n") + 1
                        findings.append(Finding(
                            concern=ConcernType.DOCUMENTATION,
                            severity=Severity(severity),
                            file=path,
                            line_start=matched_line,
                            line_end=matched_line,
                            summary=f"{name.replace('_', ' ').title()}",
                            explanation=f"{explanation} at {path}:{matched_line}",
                            suggested_fix_diff=None,
                            confidence=0.4 if severity == "info" else 0.55,
                            evidence=[EvidenceItem(tool="docs_analyzer", query=pattern, result=name)],
                            agent_version=self.AGENT_VERSION,
                            prompt_hash=self._prompt_hash(name, path, str(matched_line)),
                        ))
                else:
                    if actual_pattern.lower() in scan_text.lower():
                        lines = scan_text.split("\n")
                        for i, line in enumerate(lines, start=1):
                            if actual_pattern.lower() in line.lower():
                                findings.append(Finding(
                                    concern=ConcernType.DOCUMENTATION,
                                    severity=Severity(severity),
                                    file=path,
                                    line_start=i,
                                    line_end=i,
                                    summary=f"{name.replace('_', ' ').title()}",
                                    explanation=f"{explanation} at {path}:{i}",
                                    suggested_fix_diff=None,
                                    confidence=0.4 if severity == "info" else 0.55,
                                    evidence=[EvidenceItem(tool="docs_analyzer", query=pattern, result=name)],
                                    agent_version=self.AGENT_VERSION,
                                    prompt_hash=self._prompt_hash(name, path, str(i)),
                                ))
                                break

        return findings
