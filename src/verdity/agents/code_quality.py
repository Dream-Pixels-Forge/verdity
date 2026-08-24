"""
Code Quality Specialist Agent.

Reviews PR diffs for code style, maintainability, and structural issues.
Produces schema-valid findings with deterministic confidence scores.
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

# ── Code quality patterns (deterministic rules) ──────────────────────

QUALITY_PATTERNS: list[tuple[str, str, str, str]] = [
    # (name, pattern, severity, explanation)
    ("long_function", "def .*\\(.*\\):\\n.{200,}", "medium", "Function may be too long — consider refactoring"),
    ("deep_nesting", "    {5,}", "low", "Deep nesting reduces readability — consider early returns"),
    ("todo_comment", "TODO", "info", "TODO comment found — track for follow-up"),
    ("fixme_comment", "FIXME", "low", "FIXME comment found — should be addressed before merge"),
    ("magic_number", "= \\d{4,}", "low", "Magic number — consider a named constant"),
    ("bare_except", "except:", "medium", "Bare except catches all exceptions — specify exception types"),
    ("global_import", "from .* import \\*", "high", "Wildcard import pollutes namespace — import names explicitly"),
    ("assert_in_code", "assert ", "medium", "Assert statement in production code — removes on optimize"),
    ("print_statement", "print\\(", "low", "Debug print statement — remove before merge"),
]


class CodeQualityAgent(BaseSpecialistAgent):
    """Code quality specialist agent for Verdity."""

    AGENT_VERSION = "code-quality-agent@0.1.0"
    SPECIALIST_NAME = "code_quality"
    CONCERN_TYPE = ConcernType.CODE_QUALITY
    _input_tokens_per_finding = 300
    _output_tokens_per_finding = 50

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

            for name, pattern, severity, explanation in QUALITY_PATTERNS:
                if pattern.lower() in scan_text.lower():
                    lines = scan_text.split("\n")
                    for i, line in enumerate(lines, start=1):
                        if pattern.lower() in line.lower():
                            findings.append(Finding(
                                concern=ConcernType.CODE_QUALITY,
                                severity=Severity(severity),
                                file=path,
                                line_start=i,
                                line_end=i,
                                summary=f"{name.replace('_', ' ').title()} detected",
                                explanation=f"{explanation} at {path}:{i}",
                                suggested_fix_diff=self._suggested_fix(name),
                                confidence=0.6 if severity == "info" else 0.7,
                                evidence=[EvidenceItem(tool="code_quality_linter", query=pattern, result=name)],
                                agent_version=self.AGENT_VERSION,
                                prompt_hash=self._prompt_hash(name, path, str(i)),
                            ))
                            break

        return findings

    @staticmethod
    def _suggested_fix(name: str) -> str | None:
        fixes = {
            "bare_except": "- except:\n+ except Exception as e:\n    logger.error(...)",
            "global_import": "- from module import *\n+ from module import specific_name",
            "print_statement": "- print(x)\n+ logging.debug(%r, x)",
        }
        return fixes.get(name)
