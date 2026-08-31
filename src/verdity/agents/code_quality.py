"""
Code Quality Specialist Agent.

Reviews PR diffs for code style, maintainability, and structural issues.
Produces schema-valid findings with deterministic confidence scores.
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

# ── Code quality patterns (deterministic rules) ──────────────────────

QUALITY_PATTERNS: list[tuple[str, str, str, str]] = [
    # (name, pattern, severity, explanation)
    # Patterns prefixed with "re:" use regex search; otherwise plain substring match.
    (
        "long_function",
        "re:def .*\\(.*\\):\\n.{200,}",
        "medium",
        "Function may be too long — consider refactoring",
    ),
    (
        "deep_nesting",
        "    {5,}",
        "low",
        "Deep nesting reduces readability — consider early returns",
    ),
    ("todo_comment", "TODO", "info", "TODO comment found — track for follow-up"),
    ("fixme_comment", "FIXME", "low", "FIXME comment found — should be addressed before merge"),
    ("magic_number", "= \\d{4,}", "low", "Magic number — consider a named constant"),
    (
        "bare_except",
        "except:",
        "medium",
        "Bare except catches all exceptions — specify exception types",
    ),
    (
        "global_import",
        "from .* import \\*",
        "high",
        "Wildcard import pollutes namespace — import names explicitly",
    ),
    (
        "assert_in_code",
        "assert ",
        "medium",
        "Assert statement in production code — removes on optimize",
    ),
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
                is_regex = pattern.startswith("re:")
                actual_pattern = pattern[3:] if is_regex else pattern

                matched = False
                matched_line = 0

                if is_regex:
                    # Use regex search on the full text
                    m = re.search(actual_pattern, scan_text, re.DOTALL)
                    if m:
                        matched = True
                        matched_line = scan_text[: m.start()].count("\n") + 1
                else:
                    # Plain substring match (case-insensitive)
                    if actual_pattern.lower() in scan_text.lower():
                        matched = True
                        # Find the specific line
                        for i, line in enumerate(scan_text.split("\n"), start=1):
                            if actual_pattern.lower() in line.lower():
                                matched_line = i
                                break

                if matched:
                    findings.append(
                        Finding(
                            concern=ConcernType.CODE_QUALITY,
                            severity=Severity(severity),
                            file=path,
                            line_start=matched_line,
                            line_end=matched_line,
                            summary=f"{name.replace('_', ' ').title()} detected",
                            explanation=f"{explanation} at {path}:{matched_line}",
                            suggested_fix_diff=self._suggested_fix(name),
                            confidence=0.6 if severity == "info" else 0.7,
                            evidence=[
                                EvidenceItem(tool="code_quality_linter", query=pattern, result=name)
                            ],
                            agent_version=self.AGENT_VERSION,
                            prompt_hash=self._prompt_hash(name, path, str(matched_line)),
                        )
                    )

        return findings

    @staticmethod
    def _suggested_fix(name: str) -> str | None:
        fixes = {
            "bare_except": "- except:\n+ except Exception as e:\n    logger.error(...)",
            "global_import": "- from module import *\n+ from module import specific_name",
            "print_statement": "- print(x)\n+ logging.debug(%r, x)",
        }
        return fixes.get(name)
