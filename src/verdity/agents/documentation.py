"""
Documentation Specialist Agent.

Reviews PR diffs for documentation quality and completeness.
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

DOC_PATTERNS: list[tuple[str, str, str, str]] = [
    ("missing_docstring", "def ", "info", "Function added — verify docstring coverage"),
    ("missing_type_hints", "def ", "low", "Function may lack type hints"),
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
                if pattern.lower() in scan_text.lower():
                    lines = scan_text.split("\n")
                    for i, line in enumerate(lines, start=1):
                        if pattern.lower() in line.lower():
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
