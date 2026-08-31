"""
Security Specialist Agent.

Produces structured, evidence-backed security findings for PR diffs.
Tools available: semantic_search, secret_scanner, cve_lookup (mocked in dev).

Non-negotiable constraints satisfied:
  - #4: Uses the shared SemanticIndex (no private vector store)
  - #5: Confidence computed by deterministic post-processing, never raw LLM self-report
  - #8: Every model/tool call metered through TokenEconomicsService
  - #9: Every finding logged to Audit Store
"""

from __future__ import annotations

import logging
import re

from verdity.agents.base import BaseSpecialistAgent
from verdity.schemas import (
    ConcernType,
    EvidenceItem,
    Finding,
    Severity,
    SpecialistContext,
)
from verdity.semantic_index import SemanticIndex

logger = logging.getLogger(__name__)

# ── Security scan patterns (deterministic rules, no LLM needed for these) ──

_SECRET_PATTERNS: list[tuple[str, str, str]] = [
    # (name, regex-like substring, severity)
    ("AWS_ACCESS_KEY", "aws_access_key", "high"),
    ("AWS_SECRET_KEY", "aws_secret_key", "high"),
    ("PRIVATE_KEY", "BEGIN PRIVATE KEY", "critical"),
    ("GITHUB_TOKEN", "ghp_", "high"),
    ("GITHUB_TOKEN_ALT", "github_pat_", "high"),
    ("SLACK_TOKEN", "xoxb-", "medium"),
    ("SLACK_TOKEN_USER", "xoxp-", "medium"),
    ("JWT_SECRET", "jwtSecret", "high"),
    ("API_KEY_ASSIGN", '= "sk-', "high"),
    ("HARDCODED_PASSWORD", "password = '", "high"),
]

_CWE_MAPPING: dict[str, str] = {
    "AWS_ACCESS_KEY": "CWE-798 (Use of Hard-coded Credentials)",
    "PRIVATE_KEY": "CWE-321 (Use of Hard-coded Cryptographic Key)",
    "GITHUB_TOKEN": "CWE-798",
    "GITHUB_TOKEN_ALT": "CWE-798",
    "SLACK_TOKEN": "CWE-798",
    "JWT_SECRET": "CWE-798",
    "API_KEY_ASSIGN": "CWE-798",
    "HARDCODED_PASSWORD": "CWE-798",
}

# ── Vulnerability patterns (compiled regex for performance) ────────────
# Pattern: (name, pattern_str, compiled_regex, severity, explanation)
_VULN_PATTERNS: list[tuple[str, str, re.Pattern[str], str, str]] = [
    (name, pat, re.compile(pat, re.IGNORECASE), sev, desc)
    for name, pat, sev, desc in [
        ("sql_injection", r'f"SELECT', "high", "Potential SQL injection via f-string"),
        ("sql_injection2", r"' \+ request", "high", "Potential SQL injection via string concat"),
        ("eval_usage", r"\beval\(", "critical", "Use of eval() — potential code injection"),
        ("exec_usage", r"\bexec\(", "critical", "Use of exec() — potential code injection"),
        (
            "shell_injection",
            r"subprocess\.call.*shell\s*=\s*True",
            "critical",
            "Shell injection risk",
        ),
        ("os_system", r"os\.system\(", "high", "os.system() — potential command injection"),
        ("pickle_load", r"pickle\.load", "high", "Unsafe deserialization via pickle"),
        ("yaml_unsafe", r"yaml\.load\(", "medium", "Unsafe YAML load — use yaml.safe_load"),
        ("path_traversal", r"\.\./", "medium", "Potential path traversal — validate input"),
        ("weak_hash", r"\bmd5\(", "medium", "Weak hash function — use SHA-256 or stronger"),
        ("insecure_random", r"random\.randint", "low", "Cryptographically weak random"),
    ]
]


class SecurityAgent(BaseSpecialistAgent):
    """
    Security specialist agent for Verdity.

    Performs deterministic static analysis + semantic search to produce
    security findings with cited evidence and computed confidence scores.
    """

    AGENT_VERSION = "security-agent@0.1.0"
    SPECIALIST_NAME = "security"
    CONCERN_TYPE = ConcernType.SECURITY
    _input_tokens_per_finding = 500
    _output_tokens_per_finding = 50

    async def _scan(
        self,
        ctx: SpecialistContext,
        semantic_index: SemanticIndex,
    ) -> list[Finding]:
        findings: list[Finding] = []

        # ── Pass 1: Deterministic rule-based scans (no LLM cost) ──────
        rule_findings = self._scan_for_secrets(ctx.diff_files)
        findings.extend(rule_findings)

        # ── Pass 2: Semantic search for security-relevant patterns ────
        security_queries = [
            "authentication",
            "authorization",
            "session",
            "token",
            "password",
            "crypto",
            "hash",
            "encryption",
            "SQL injection",
            "XSS",
            "CSRF",
            "deserialization",
            "file upload",
        ]
        semantic_findings = await self._semantic_security_search(
            ctx=ctx,
            queries=security_queries,
            semantic_index=semantic_index,
        )
        findings.extend(semantic_findings)

        # ── Pass 3: Diff-aware analysis (check added lines specifically) ─
        diff_findings = self._scan_diff_for_vulnerabilities(ctx.diff_files)
        findings.extend(diff_findings)

        return findings

    # ── Rule-Based Scans ──────────────────────────────────────────────

    def _scan_for_secrets(self, diff_files: list[dict]) -> list[Finding]:
        """Deterministic scan for hard-coded secrets in diff content."""
        findings: list[Finding] = []
        for file_info in diff_files:
            path = file_info.get("path", "")
            content = file_info.get("content", "")
            additions = file_info.get("additions", "")

            scan_text = additions if additions else content

            for pattern_name, pattern_str, severity_str in _SECRET_PATTERNS:
                if pattern_str.lower() in scan_text.lower():
                    lines = scan_text.split("\n")
                    for i, line in enumerate(lines, start=1):
                        if pattern_str.lower() in line.lower():
                            severity = self._str_to_severity(severity_str)
                            cwe = _CWE_MAPPING.get(pattern_name, "CWE-798")
                            findings.append(
                                Finding(
                                    concern=ConcernType.SECURITY,
                                    severity=severity,
                                    file=path,
                                    line_start=i,
                                    line_end=i,
                                    summary=f"Potential {pattern_name.replace('_', ' ')} detected",
                                    explanation=f"Pattern '{pattern_str}' found in {path}:{i}. "
                                    f"This may be a hard-coded credential. {cwe}",
                                    suggested_fix_diff=None,
                                    confidence=self._compute_secret_confidence(pattern_name, line),
                                    evidence=[
                                        EvidenceItem(
                                            tool="secret_scanner",
                                            result=cwe,
                                            query=pattern_str,
                                        )
                                    ],
                                    agent_version=self.AGENT_VERSION,
                                    prompt_hash=self._prompt_hash("secret_scan", path, str(i)),
                                )
                            )
                            break
        return findings

    def _scan_diff_for_vulnerabilities(self, diff_files: list[dict]) -> list[Finding]:
        """Scan diff content for common vulnerability patterns using regex."""
        findings: list[Finding] = []

        for file_info in diff_files:
            path = file_info.get("path", "")
            content = file_info.get("content", "")
            additions = file_info.get("additions", "")
            scan_text = additions if additions else content

            for name, pattern_str, compiled_re, severity, explanation in _VULN_PATTERNS:
                match = compiled_re.search(scan_text)
                if match:
                    # Find the line number of the match
                    line_start = scan_text[: match.start()].count("\n") + 1
                    findings.append(
                        Finding(
                            concern=ConcernType.SECURITY,
                            severity=self._str_to_severity(severity),
                            file=path,
                            line_start=line_start,
                            line_end=line_start,
                            summary=f"{name.replace('_', ' ').title()} detected",
                            explanation=f"{explanation} at {path}:{line_start}",
                            suggested_fix_diff=self._suggested_fix(name),
                            confidence=0.75 if severity in ("high", "critical") else 0.55,
                            evidence=[
                                EvidenceItem(
                                    tool="static_analyzer",
                                    result=name,
                                    query=pattern_str,
                                )
                            ],
                            agent_version=self.AGENT_VERSION,
                            prompt_hash=self._prompt_hash("vuln_scan", path, str(line_start)),
                        )
                    )
        return findings

    # ── Semantic Security Search ──────────────────────────────────────

    async def _semantic_security_search(
        self,
        *,
        ctx: SpecialistContext,
        queries: list[str],
        semantic_index: SemanticIndex,
    ) -> list[Finding]:
        findings: list[Finding] = []
        diff_paths = {fi.get("path", "") for fi in ctx.diff_files}

        for query in queries:
            try:
                results = await semantic_index.search_by_text(
                    f"{ctx.repo_owner}/{ctx.repo_name}",
                    query,
                    limit=3,
                )
                for chunk in results:
                    if chunk["file_path"] not in diff_paths:
                        continue
                    content = chunk["content"]
                    security_keywords = ["auth", "token", "password", "secret", "key", "session"]
                    if any(kw in content.lower() for kw in security_keywords):
                        findings.append(
                            Finding(
                                concern=ConcernType.SECURITY,
                                severity=self._str_to_severity("medium"),
                                file=chunk["file_path"],
                                line_start=chunk["start_line"],
                                line_end=chunk["end_line"],
                                summary=f"Security-relevant code near search term: '{query}'",
                                explanation=(
                                    f"File {chunk['file_path']} contains both '{query}' and "
                                    f"security keywords. Review for proper handling."
                                ),
                                suggested_fix_diff=None,
                                confidence=0.45,
                                evidence=[
                                    EvidenceItem(
                                        tool="semantic_search",
                                        query=query,
                                        result=f"matched at {chunk['file_path']}:{chunk['start_line']}",
                                    )
                                ],
                                agent_version=self.AGENT_VERSION,
                                prompt_hash=self._prompt_hash(
                                    "semantic_search", query, chunk["file_path"]
                                ),
                            )
                        )
            except Exception as exc:
                logger.debug("Semantic search for '%s' failed: %s", query, exc)

        return findings

    # ── Confidence Computation (deterministic, per Orchestration doc §4) ─

    def _compute_secret_confidence(self, pattern_name: str, line: str) -> float:
        if pattern_name in ("PRIVATE_KEY",):
            base_confidence = 0.95
        elif pattern_name in ("AWS_ACCESS_KEY", "AWS_SECRET_KEY"):
            base_confidence = 0.90
        else:
            base_confidence = 0.85

        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("*"):
            base_confidence = max(0.1, base_confidence - 0.3)

        return round(base_confidence, 2)

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _str_to_severity(s: str) -> Severity:
        return Severity(s.lower())

    @staticmethod
    def _suggested_fix(vuln_name: str) -> str | None:
        fixes: dict[str, str] = {
            "sql_injection": '- cursor.execute(f"SELECT ...")\n+ cursor.execute("SELECT ... WHERE id = %s", (user_id,))',
            "eval_usage": "- result = eval(user_input)\n+ result = ast.literal_eval(user_input)  # or a safer alternative",
            "exec_usage": "- exec(user_code)\n+ # Avoid exec; use a sandboxed environment or pre-compiled functions",
            "pickle_load": "- data = pickle.load(f)\n+ data = json.load(f)  # or use a safe deserializer",
            "yaml_unsafe": "- yaml.load(stream)\n+ yaml.safe_load(stream)",
            "weak_hash": "- hashlib.md5(data)\n+ hashlib.sha256(data)",
        }
        return fixes.get(vuln_name)
