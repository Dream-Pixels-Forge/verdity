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
        *,
        use_llm: bool = False,
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

        # ── Pass 4: LLM-enhanced analysis (optional, when use_llm=True) ─
        if use_llm:
            llm_findings = await self._llm_enhanced_scan(ctx)
            findings.extend(llm_findings)

        return findings

    # ── LLM-Enhanced Security Scan (Pass 4) ─────────────────────────

    async def _llm_enhanced_scan(self, ctx: SpecialistContext) -> list[Finding]:
        """
        Run LLM-enhanced security analysis after deterministic regex.
        Finds logic issues, context-dependent bugs, and nuance that regex misses.

        LLM is optional — if no client is available, returns empty list.
        """
        if not ctx.llm_client or not ctx.llm_client.enabled:
            return []

        findings: list[Finding] = []
        diff_text = "\n".join(
            f"--- {f.get('path', 'unknown')}\n{f.get('additions', f.get('content', ''))}"
            for f in ctx.diff_files
        )
        if not diff_text.strip():
            return []

        system_prompt = (
            "You are a senior security engineer reviewing a code diff for security vulnerabilities. "
            "Focus on LOGIC flaws that static analysis misses: auth bypass, privilege escalation, "
            "injection chains, race conditions, unsafe deserialization paths, improper input validation, "
            "and business logic bugs that could lead to security issues.\n\n"
            "Return a JSON array of findings. Each finding must have:\n"
            "- summary: short description\n"
            "- severity: critical|high|medium|low|info\n"
            "- file: file path from the diff\n"
            "- line_start: line number (approximate)\n"
            "- explanation: detailed explanation of the security concern\n"
            "- suggested_fix: brief code suggestion or 'none'\n\n"
            "Only report genuine security concerns. Do NOT report style issues or trivial matters.\n"
            "If no security issues found, return an empty array: []"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Review this diff for security vulnerabilities:\n\n{diff_text}"},
        ]

        try:
            response = await ctx.llm_client.complete(
                model="gpt-4o",
                messages=messages,
                temperature=0.0,
                max_tokens=4096,
            )
            parsed = self._parse_llm_security_response(response.content)
            for item in parsed:
                severity_str = item.get("severity", "medium").lower()
                try:
                    severity = Severity(severity_str)
                except ValueError:
                    severity = Severity.MEDIUM
                findings.append(
                    Finding(
                        concern=ConcernType.SECURITY,
                        severity=severity,
                        file=item.get("file", "unknown"),
                        line_start=item.get("line_start", 1),
                        line_end=item.get("line_start", 1),
                        summary=f"[LLM] {item.get('summary', 'Security concern')}",
                        explanation=item.get("explanation", ""),
                        suggested_fix_diff=(
                            item.get("suggested_fix")
                            if item.get("suggested_fix", "none").lower() != "none"
                            else None
                        ),
                        confidence=0.70,  # LLM findings get base confidence; trust calibration adjusts
                        evidence=[
                            EvidenceItem(
                                tool="llm_security_analyst",
                                result=item.get("summary", ""),
                                query="llm_enhanced_scan",
                            )
                        ],
                        agent_version=self.AGENT_VERSION,
                        prompt_hash=self._prompt_hash("llm_security", str(ctx.review_run_id)),
                    )
                )
        except Exception as exc:
            logger.warning("LLM security scan failed: %s", exc)

        return findings

    @staticmethod
    def _parse_llm_security_response(content: str) -> list[dict]:
        """Parse LLM security response, extracting JSON array from markdown or raw text."""
        import json as _json
        import re as _re

        # Try code block
        block_match = _re.search(r"```(?:json)?\s*\n(.*?)\n```", content, _re.DOTALL)
        if block_match:
            try:
                return _json.loads(block_match.group(1))
            except _json.JSONDecodeError:
                pass

        # Try raw array
        arr_match = _re.search(r"\[.*\]", content, _re.DOTALL)
        if arr_match:
            try:
                return _json.loads(arr_match.group(0))
            except _json.JSONDecodeError:
                pass

        return []

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
        if stripped.startswith(("#", "//", "*")):
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
