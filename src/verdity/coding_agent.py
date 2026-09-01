"""
Coding Agent.

Takes a Finding and produces a proposed code fix (diff).
Deterministic rule-based fix generation — no LLM in dev mode.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from verdity.schemas import ConcernType, Finding

logger = logging.getLogger(__name__)

AGENT_VERSION = "coding-agent@0.1.0"


@dataclass
class ProposedFix:
    """A proposed code change for a single finding."""

    finding_id: uuid.UUID
    file: str
    original_line: int
    suggested_lines: list[str]
    explanation: str
    fix_type: str  # "secret_removal", "sql_fix", "hash_fix", etc.


class CodingAgent:
    """Produces deterministic code fixes for security and quality findings."""

    AGENT_VERSION = AGENT_VERSION

    def propose_fix(self, finding: Finding) -> ProposedFix | None:
        """Generate a fix proposal for the given finding. Returns None if no fix available."""
        concern = finding.concern

        if concern == ConcernType.SECURITY:
            return self._fix_security(finding)
        elif concern == ConcernType.CODE_QUALITY:
            return self._fix_quality(finding)
        return None

    def _fix_security(self, finding: Finding) -> ProposedFix | None:
        summary = finding.summary.lower()

        if "password" in summary or "secret" in summary or "credential" in summary:
            return ProposedFix(
                finding_id=finding.finding_id,
                file=finding.file,
                original_line=finding.line_start,
                suggested_lines=[
                    "# TODO: Replace hard-coded credential with environment variable",
                    f"# Original: {finding.file}:{finding.line_start}",
                    "import os",
                    "cred = os.environ.get('CREDENTIAL_NAME')",
                ],
                explanation="Hard-coded credential should use environment variable or secrets manager",
                fix_type="secret_removal",
            )
        elif "sql" in summary and ("injection" in summary or 'f"' in finding.explanation):
            return ProposedFix(
                finding_id=finding.finding_id,
                file=finding.file,
                original_line=finding.line_start,
                suggested_lines=[
                    "# Use parameterized query instead of f-string",
                    f"# Original at {finding.file}:{finding.line_start}",
                    'cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))',
                ],
                explanation="Replace string concatenation with parameterized query",
                fix_type="sql_fix",
            )
        elif "eval" in summary or "exec" in summary:
            return ProposedFix(
                finding_id=finding.finding_id,
                file=finding.file,
                original_line=finding.line_start,
                suggested_lines=[
                    "# Use ast.literal_eval or a safe alternative",
                    f"# Original at {finding.file}:{finding.line_start}",
                    "import ast",
                    "result = ast.literal_eval(user_input)  # Safe alternative to eval()",
                ],
                explanation="Replace eval/exec with safe alternatives",
                fix_type="eval_replacement",
            )
        elif "pickle" in summary:
            return ProposedFix(
                finding_id=finding.finding_id,
                file=finding.file,
                original_line=finding.line_start,
                suggested_lines=[
                    "# Use JSON instead of pickle for serialization",
                    f"# Original at {finding.file}:{finding.line_start}",
                    "import json",
                    "data = json.loads(raw_bytes)",
                ],
                explanation="Replace pickle.load with json.load for safe deserialization",
                fix_type="pickle_replacement",
            )
        elif "hash" in summary and "md5" in summary:
            return ProposedFix(
                finding_id=finding.finding_id,
                file=finding.file,
                original_line=finding.line_start,
                suggested_lines=[
                    "# Use SHA-256 instead of MD5",
                    f"# Original at {finding.file}:{finding.line_start}",
                    "import hashlib",
                    "digest = hashlib.sha256(data).hexdigest()",
                ],
                explanation="Replace weak MD5 hash with SHA-256",
                fix_type="hash_fix",
            )
        return None

    def _fix_quality(self, finding: Finding) -> ProposedFix | None:
        summary = finding.summary.lower()

        if "bare except" in summary:
            return ProposedFix(
                finding_id=finding.finding_id,
                file=finding.file,
                original_line=finding.line_start,
                suggested_lines=[
                    "# Catch specific exceptions instead of bare except",
                    f"# Original at {finding.file}:{finding.line_start}",
                    "except (ValueError, TypeError) as exc:",
                    "    logger.error('Invalid input: %s', exc)",
                ],
                explanation="Bare except catches all exceptions including KeyboardInterrupt",
                fix_type="except_specific",
            )
        if "wildcard import" in summary or "global import" in summary:
            return ProposedFix(
                finding_id=finding.finding_id,
                file=finding.file,
                original_line=finding.line_start,
                suggested_lines=[
                    "# Import specific names instead of wildcard",
                    f"# Original at {finding.file}:{finding.line_start}",
                    "from module import specific_name1, specific_name2",
                ],
                explanation="Wildcard imports pollute namespace and make static analysis harder",
                fix_type="import_specific",
            )
        if "print" in summary:
            return ProposedFix(
                finding_id=finding.finding_id,
                file=finding.file,
                original_line=finding.line_start,
                suggested_lines=[
                    "# Replace print with logging",
                    f"# Original at {finding.file}:{finding.line_start}",
                    "import logging",
                    "logger = logging.getLogger(__name__)",
                    "logger.debug('value = %s', value)",
                ],
                explanation="Debug print statements should use the logging module",
                fix_type="print_to_logging",
            )
        return None
