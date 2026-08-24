"""
Base specialist agent — shared boilerplate for all Verdity specialist agents.

Eliminates duplication across security, code_quality, testing, and documentation
agents by providing common run orchestration, token metering, and audit logging.
"""

from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod

from verdity.audit_store import AuditStore
from verdity.schemas import (
    ConcernType,
    Finding,
    SpecialistContext,
    SpecialistResponse,
)
from verdity.semantic_index import SemanticIndex
from verdity.token_economics import TokenEconomicsService

logger = logging.getLogger(__name__)


class BaseSpecialistAgent(ABC):
    """Abstract base for all specialist agents. Handles metering and audit."""

    AGENT_VERSION: str = "base-agent@0.0.0"
    SPECIALIST_NAME: str = "base"
    CONCERN_TYPE: ConcernType = ConcernType.SECURITY

    # Subclasses override these for token estimation
    _input_tokens_per_finding: int = 300
    _output_tokens_per_finding: int = 50

    async def run(
        self,
        ctx: SpecialistContext,
        semantic_index: SemanticIndex,
        token_economics: TokenEconomicsService,
        audit_store: AuditStore,
    ) -> SpecialistResponse:
        """
        Template method: scan → record tokens → audit-log findings → return.
        Subclasses implement `_scan()` for the actual analysis.
        """
        findings = await self._scan(ctx, semantic_index)

        input_tokens = len(findings) * self._input_tokens_per_finding
        output_tokens = len(findings) * self._output_tokens_per_finding

        await token_economics.record_call(
            review_run_id=ctx.review_run_id,
            agent_name=self.AGENT_VERSION,
            model=f"{self.SPECIALIST_NAME}/dev",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            repo_owner=ctx.repo_owner,
            repo_name=ctx.repo_name,
            org=ctx.repo_owner,
        )

        for finding in findings:
            await audit_store.append(
                event_type="finding.created",
                entity_type="finding",
                entity_id=str(finding.finding_id),
                payload={
                    "concern": finding.concern.value,
                    "severity": finding.severity.value,
                    "file": finding.file,
                    "summary": finding.summary,
                    "confidence": finding.confidence,
                    "agent_version": self.AGENT_VERSION,
                },
                related_run_id=ctx.review_run_id,
            )

        logger.info(
            "%s run %s: %d findings",
            self.SPECIALIST_NAME, ctx.review_run_id, len(findings),
        )
        return SpecialistResponse(
            review_run_id=ctx.review_run_id,
            specialist=self.SPECIALIST_NAME,
            status="complete",
            findings=findings,
            tokens_used={"input": input_tokens, "output": output_tokens},
            cost_usd=0.0,
        )

    @abstractmethod
    async def _scan(
        self,
        ctx: SpecialistContext,
        semantic_index: SemanticIndex,
    ) -> list[Finding]:
        """Subclass implements the actual scan logic."""
        ...

    @staticmethod
    def _prompt_hash(*parts: str) -> str:
        return "sha256:" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
