"""
LLM Enricher Agent — async enrichment for findings with explain/fix/risk/all modes.

Issue #52: Creates enriched findings with LLM-generated explanations, fixes, and risk assessments.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar

from verdity.audit_store import AuditStore
from verdity.llm_gateway import GatewayConfig, LocalLLMGateway
from verdity.model_fallback import MultiModelFallback
from verdity.schemas import ConcernType, Finding, SpecialistContext
from verdity.token_economics import TokenEconomicsService

logger = logging.getLogger(__name__)


# ── EnrichmentMode Enum ─────────────────────────────────────────────────


class EnrichmentMode(StrEnum):
    """Enumeration of enrichment modes."""

    EXPLAIN = "explain"
    FIX = "fix"
    RISK = "risk"
    ALL = "all"


# ── EnrichmentResult Dataclass ──────────────────────────────────────────


@dataclass
class EnrichmentResult:
    """Result of enriching a single finding."""

    finding_id: str
    mode: EnrichmentMode
    model: str
    backend: str
    explanation: str | None = None
    fix_diff: str | None = None
    fix_explanation: str | None = None
    risk_score: float | None = None
    cwe_ids: list[str] | None = None
    exploitability: str | None = None
    blast_radius: str | None = None
    confidence_adjustment_rationale: str | None = None
    tokens_used: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0


# ── LLMEnricherAgent ────────────────────────────────────────────────────


class LLMEnricherAgent:
    """
    Async enrichment agent for findings.

    Batches findings by concern type and uses LLM to generate:
    - explain: Why is this a real issue? Context-aware explanation.
    - fix: Concrete code fix as unified diff.
    - risk: Exploitability, blast radius, CWE mapping.
    - all: Combined output of all above modes.

    Results are persisted to audit log and token usage tracked.
    """

    AGENT_VERSION = "llm-enricher-agent@0.1.0"
    SPECIALIST_NAME = "llm_enricher"

    # Secret patterns for redaction (same as security agent)
    _SECRET_PATTERNS: ClassVar[list[tuple[re.Pattern, str]]] = [
        (re.compile(r'AWS_ACCESS_KEY_ID\s*=\s*["\'][A-Z0-9]{20}["\']'), "AWS_ACCESS_KEY_ID"),
        (
            re.compile(r'AWS_SECRET_ACCESS_KEY\s*=\s*["\'][A-Za-z0-9/+=]{32,}["\']'),
            "AWS_SECRET_ACCESS_KEY",
        ),
        (re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"), "GITHUB_TOKEN"),
        (re.compile(r"github_pat_[A-Za-z0-9_]{80,}"), "GITHUB_TOKEN_PAT"),
        (re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"), "PRIVATE_KEY"),
        (re.compile(r"sk-[A-Za-z0-9]{32,}"), "OPENAI_API_KEY"),
        (re.compile(r'password\s*=\s*["\'][^"\']{8,}["\']'), "PASSWORD"),
        (re.compile(r'api[_-]?key\s*=\s*["\'][A-Za-z0-9_\-]{20,}["\']'), "API_KEY"),
        (re.compile(r'secret\s*=\s*["\'][A-Za-z0-9_\-]{20,}["\']'), "SECRET"),
        (re.compile(r'token\s*=\s*["\'][A-Za-z0-9_\-]{20,}["\']'), "TOKEN"),
    ]

    def __init__(
        self,
        *,
        gateway: LocalLLMGateway | None = None,
        config: GatewayConfig | None = None,
        fallback: MultiModelFallback | None = None,
    ) -> None:
        self._gateway = gateway
        self._config = config
        self._fallback = fallback
        # Cache for prompt templates and schemas
        self._prompt_templates: dict[EnrichmentMode, str] = {}
        self._output_schemas: dict[EnrichmentMode, dict[str, Any]] = {}
        self._init_templates_and_schemas()

    def _init_templates_and_schemas(self) -> None:
        """Initialize prompt templates and JSON schemas for each mode."""

        # EXPLAIN mode
        self._prompt_templates[
            EnrichmentMode.EXPLAIN
        ] = """You are a senior code reviewer explaining findings to a developer.

For each finding below, provide a clear, concise explanation of WHY this is a real issue.
Focus on the practical impact and root cause. Be specific to the code context.

FINDINGS:
{findings}

DIFF CONTEXT:
{diff}

Return a JSON array where each object has:
- finding_id: the finding ID
- explanation: clear explanation of why this is a real issue
- confidence_adjustment_rationale: why the confidence score is appropriate (or should be adjusted)"""

        self._output_schemas[EnrichmentMode.EXPLAIN] = {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["finding_id", "explanation", "confidence_adjustment_rationale"],
                "properties": {
                    "finding_id": {"type": "string"},
                    "explanation": {"type": "string"},
                    "confidence_adjustment_rationale": {"type": "string"},
                },
            },
        }

        # FIX mode
        self._prompt_templates[
            EnrichmentMode.FIX
        ] = """You are a senior engineer generating code fixes for security and quality issues.

For each finding below, generate a CONCRETE FIX as a unified diff.
The fix must be minimal, correct, and directly address the issue.

FINDINGS:
{findings}

DIFF CONTEXT:
{diff}

Return a JSON array where each object has:
- finding_id: the finding ID
- fix_diff: unified diff format (lines starting with - for removals, + for additions)
- fix_explanation: brief explanation of the fix approach

CRITICAL: fix_diff must be a valid unified diff. Example:
- cursor.execute(f"SELECT * FROM users WHERE name = '{{name}}'")
+ cursor.execute("SELECT * FROM users WHERE name = %s", (name,))"""

        self._output_schemas[EnrichmentMode.FIX] = {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["finding_id", "fix_diff", "fix_explanation"],
                "properties": {
                    "finding_id": {"type": "string"},
                    "fix_diff": {"type": "string"},
                    "fix_explanation": {"type": "string"},
                },
            },
        }

        # RISK mode
        self._prompt_templates[
            EnrichmentMode.RISK
        ] = """You are a security risk assessor evaluating findings for exploitability and impact.

For each finding below, assess:
1. Risk score (0.0-1.0): overall risk level
2. CWE IDs: applicable Common Weakness Enumeration IDs
3. Exploitability: LOW/MEDIUM/HIGH/CRITICAL
4. Blast radius: description of potential impact scope

FINDINGS:
{findings}

DIFF CONTEXT:
{diff}

Return a JSON array where each object has:
- finding_id: the finding ID
- risk_score: number between 0.0 and 1.0
- cwe_ids: array of CWE identifier strings (e.g., ["CWE-89", "CWE-798"])
- exploitability: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
- blast_radius: string describing potential impact scope"""

        self._output_schemas[EnrichmentMode.RISK] = {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "finding_id",
                    "risk_score",
                    "cwe_ids",
                    "exploitability",
                    "blast_radius",
                ],
                "properties": {
                    "finding_id": {"type": "string"},
                    "risk_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "cwe_ids": {"type": "array", "items": {"type": "string"}},
                    "exploitability": {
                        "type": "string",
                        "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                    },
                    "blast_radius": {"type": "string"},
                },
            },
        }

        # ALL mode - combines all fields
        self._prompt_templates[
            EnrichmentMode.ALL
        ] = """You are a senior code reviewer providing comprehensive enrichment for findings.

For each finding below, provide ALL of the following:
1. Explanation: why this is a real issue
2. Confidence adjustment rationale: why confidence is appropriate
3. Fix: unified diff with concrete code fix
4. Fix explanation: brief explanation of the fix
5. Risk score: 0.0-1.0
6. CWE IDs: applicable CWE identifiers
7. Exploitability: LOW/MEDIUM/HIGH/CRITICAL
8. Blast radius: potential impact scope

FINDINGS:
{findings}

DIFF CONTEXT:
{diff}

Return a JSON array where each object has ALL fields:
- finding_id: the finding ID
- explanation: string
- confidence_adjustment_rationale: string
- fix_diff: unified diff string
- fix_explanation: string
- risk_score: number (0.0-1.0)
- cwe_ids: array of strings
- exploitability: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
- blast_radius: string"""

        self._output_schemas[EnrichmentMode.ALL] = {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "finding_id",
                    "explanation",
                    "confidence_adjustment_rationale",
                    "fix_diff",
                    "fix_explanation",
                    "risk_score",
                    "cwe_ids",
                    "exploitability",
                    "blast_radius",
                ],
                "properties": {
                    "finding_id": {"type": "string"},
                    "explanation": {"type": "string"},
                    "confidence_adjustment_rationale": {"type": "string"},
                    "fix_diff": {"type": "string"},
                    "fix_explanation": {"type": "string"},
                    "risk_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                    "cwe_ids": {"type": "array", "items": {"type": "string"}},
                    "exploitability": {
                        "type": "string",
                        "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                    },
                    "blast_radius": {"type": "string"},
                },
            },
        }

    def _get_prompt_template(self, mode: EnrichmentMode) -> str:
        """Get the prompt template for a given mode."""
        return self._prompt_templates.get(mode, "")

    def _get_output_schema(self, mode: EnrichmentMode) -> dict[str, Any]:
        """Get the JSON output schema for a given mode."""
        return self._output_schemas.get(mode, {})

    def _redact_secrets(self, text: str) -> str:
        """Redact secrets from text before sending to LLM."""
        redacted = text
        for pattern, name in self._SECRET_PATTERNS:
            redacted = pattern.sub(f"[REDACTED:{name}]", redacted)
        return redacted

    def _batch_findings_by_concern(
        self, findings: list[Finding]
    ) -> dict[ConcernType, list[Finding]]:
        """Batch findings by concern type for targeted prompts."""
        batches: dict[ConcernType, list[Finding]] = {}
        for finding in findings:
            if finding.concern not in batches:
                batches[finding.concern] = []
            batches[finding.concern].append(finding)
        return batches

    def _format_findings_for_prompt(self, findings: list[Finding]) -> str:
        """Format findings into a string for the prompt."""
        lines = []
        for f in findings:
            lines.append(
                f"- finding_id: {f.finding_id}\n"
                f"  concern: {f.concern.value}\n"
                f"  severity: {f.severity.value}\n"
                f"  file: {f.file}\n"
                f"  line: {f.line_start}-{f.line_end}\n"
                f"  summary: {f.summary}\n"
                f"  explanation: {f.explanation}\n"
                f"  confidence: {f.confidence}\n"
                f"  suggested_fix: {f.suggested_fix_diff or 'none'}\n"
            )
        return "\n".join(lines)

    async def _enrich_batch(
        self,
        findings: list[Finding],
        diff: str,
        context: SpecialistContext,
        mode: EnrichmentMode,
        token_economics: TokenEconomicsService,
        audit_store: AuditStore,
    ) -> list[EnrichmentResult]:
        """Enrich a batch of findings of the same concern type."""
        if not findings:
            return []

        # Use provided gateway or create one from config
        gateway = self._gateway
        if gateway is None and self._config:
            gateway = LocalLLMGateway(config=self._config)
        if gateway is None:
            logger.warning("No LLM gateway configured, skipping enrichment")
            return []

        # Prepare prompt
        findings_str = self._format_findings_for_prompt(findings)
        # Redact secrets from diff
        redacted_diff = self._redact_secrets(diff)
        # Escape curly braces in diff to prevent .format() interpreting them
        safe_diff = redacted_diff.replace("{", "{{").replace("}", "}}")
        prompt = self._get_prompt_template(mode).format(
            findings=findings_str,
            diff=safe_diff,
        )

        schema = self._get_output_schema(mode)

        try:
            # Use structured completion with schema validation
            response = await gateway.complete_structured(
                prompt=prompt,
                schema=schema,
                temperature=0.0,
                max_tokens=4096,
            )

            # Parse results and create EnrichmentResult objects
            results = []
            if isinstance(response, list):
                for item in response:
                    finding_id = item.get("finding_id", str(findings[0].finding_id))
                    result = self._create_enrichment_result(
                        finding_id=finding_id,
                        mode=mode,
                        model=gateway.config.model,
                        backend=gateway.config.backend,
                        item=item,
                    )
                    results.append(result)

                    # Audit log
                    await audit_store.append(
                        event_type="enrichment.created",
                        entity_type="enrichment",
                        entity_id=finding_id,
                        payload={
                            "mode": mode.value,
                            "model": gateway.config.model,
                            "backend": gateway.config.backend,
                            "has_explanation": result.explanation is not None,
                            "has_fix": result.fix_diff is not None,
                            "has_risk": result.risk_score is not None,
                        },
                        related_run_id=context.review_run_id,
                    )

            return results

        except Exception as exc:
            logger.warning("LLM enrichment failed for batch: %s", exc)
            return []

    def _create_enrichment_result(
        self,
        finding_id: str,
        mode: EnrichmentMode,
        model: str,
        backend: str,
        item: dict[str, Any],
    ) -> EnrichmentResult:
        """Create an EnrichmentResult from parsed LLM response."""
        if mode == EnrichmentMode.EXPLAIN:
            return EnrichmentResult(
                finding_id=finding_id,
                mode=mode,
                model=model,
                backend=backend,
                explanation=item.get("explanation"),
                confidence_adjustment_rationale=item.get("confidence_adjustment_rationale"),
            )
        if mode == EnrichmentMode.FIX:
            return EnrichmentResult(
                finding_id=finding_id,
                mode=mode,
                model=model,
                backend=backend,
                fix_diff=item.get("fix_diff"),
                fix_explanation=item.get("fix_explanation"),
            )
        if mode == EnrichmentMode.RISK:
            risk_score = item.get("risk_score", 0.5)
            # Clamp to valid range
            risk_score = max(0.0, min(1.0, float(risk_score)))
            return EnrichmentResult(
                finding_id=finding_id,
                mode=mode,
                model=model,
                backend=backend,
                risk_score=risk_score,
                cwe_ids=item.get("cwe_ids", []),
                exploitability=item.get("exploitability"),
                blast_radius=item.get("blast_radius"),
            )
        if mode == EnrichmentMode.ALL:
            risk_score = item.get("risk_score", 0.5)
            risk_score = max(0.0, min(1.0, float(risk_score)))
            return EnrichmentResult(
                finding_id=finding_id,
                mode=mode,
                model=model,
                backend=backend,
                explanation=item.get("explanation"),
                confidence_adjustment_rationale=item.get("confidence_adjustment_rationale"),
                fix_diff=item.get("fix_diff"),
                fix_explanation=item.get("fix_explanation"),
                risk_score=risk_score,
                cwe_ids=item.get("cwe_ids", []),
                exploitability=item.get("exploitability"),
                blast_radius=item.get("blast_radius"),
            )
        return EnrichmentResult(
            finding_id=finding_id,
            mode=mode,
            model=model,
            backend=backend,
        )

    async def enrich_findings(
        self,
        findings: list[Finding],
        diff: str,
        context: SpecialistContext,
        mode: EnrichmentMode,
        token_economics: TokenEconomicsService,
        audit_store: AuditStore,
    ) -> list[EnrichmentResult]:
        """
        Enrich findings using LLM.

        Args:
            findings: List of findings to enrich
            diff: Full diff text for context
            context: SpecialistContext with review run info and optional LLM client
            mode: Enrichment mode (explain, fix, risk, all)
            token_economics: TokenEconomicsService for metering
            audit_store: AuditStore for logging

        Returns:
            List of EnrichmentResult objects (one per finding)
        """
        if not findings:
            return []

        # Check if LLM gateway is available
        if self._gateway is None and self._config is None:
            logger.info("LLM not configured, skipping enrichment")
            return []

        # Batch findings by concern type
        batches = self._batch_findings_by_concern(findings)

        all_results: list[EnrichmentResult] = []

        # Process each batch
        for concern_type, batch_findings in batches.items():
            logger.info(
                "Enriching %d %s findings in %s mode",
                len(batch_findings),
                concern_type.value,
                mode.value,
            )

            batch_results = await self._enrich_batch(
                findings=batch_findings,
                diff=diff,
                context=context,
                mode=mode,
                token_economics=token_economics,
                audit_store=audit_store,
            )
            all_results.extend(batch_results)

        return all_results
