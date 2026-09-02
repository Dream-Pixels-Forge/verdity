"""
Aggregator Agent.

Deduplicates, resolves conflicts, and ranks findings from multiple specialists.
Deterministic post-processing — no LLM merges everything.
"""

from __future__ import annotations

import logging
import uuid

from verdity.schemas import (
    AggregatorOutput,
    ConcernType,
    Finding,
    RankedFinding,
    RepoRef,
    Severity,
    SpecialistResponse,
)

logger = logging.getLogger(__name__)

AGENT_VERSION = "aggregator-agent@0.1.0"

# Severity ranking for conflict resolution (higher = more severe wins)
SEVERITY_RANK = {
    Severity.CRITICAL: 5,
    Severity.HIGH: 4,
    Severity.MEDIUM: 3,
    Severity.LOW: 2,
    Severity.INFO: 1,
}

# Concern priority for dedup grouping
CONCERN_ORDER = [
    ConcernType.SECURITY,
    ConcernType.CODE_QUALITY,
    ConcernType.TESTING,
    ConcernType.DOCUMENTATION,
]


class AggregatorAgent:
    """
    Aggregator agent: deduplicates, resolves conflicts, ranks findings.

    Deterministic post-processing — never asks an LLM to merge findings.
    """

    def aggregate(
        self,
        review_run_id: uuid.UUID,
        repo: RepoRef,
        responses: list[SpecialistResponse],
        confidence_threshold: float = 0.7,
    ) -> AggregatorOutput:
        """
        Merge specialist responses into a single ranked, deduped finding set.
        """
        all_findings: list[Finding] = []
        for resp in responses:
            all_findings.extend(resp.findings)

        # ── Deduplication ───────────────────────────────────────────────
        # Group by (file, line_start, concern) — findings on the same line
        # from different specialists are merged into one group.
        dedup_groups: dict[tuple[str, int, str], list[Finding]] = {}
        for f in all_findings:
            key = (f.file, f.line_start, f.concern.value)
            dedup_groups.setdefault(key, []).append(f)

        merged_findings: list[Finding] = []
        group_id_map: dict[str, uuid.UUID] = {}  # finding_id → group_id

        for group in dedup_groups.values():
            group_id = uuid.uuid4()
            # Pick the highest-confidence finding from the group
            best = max(
                group,
                key=lambda f: (
                    SEVERITY_RANK.get(f.severity, 0),
                    f.confidence,
                ),
            )
            for f in group:
                group_id_map[f.finding_id] = group_id
            merged_findings.append(best)

        # ── Confidence filtering ──────────────────────────────────────
        filtered_findings = [f for f in merged_findings if f.confidence >= confidence_threshold]

        # ── Ranking ─────────────────────────────────────────────────────
        # Composite score: severity_weight * confidence
        ranked: list[RankedFinding] = []
        for f in filtered_findings:
            sev_rank = SEVERITY_RANK.get(f.severity, 0)
            rank_score = sev_rank * f.confidence
            dedup_gid = group_id_map.get(f.finding_id)
            ranked.append(
                RankedFinding(
                    finding=f,
                    rank_score=round(rank_score, 3),
                    dedup_group_id=dedup_gid,
                )
            )

        ranked.sort(key=lambda r: r.rank_score, reverse=True)

        # ── Summary comment ─────────────────────────────────────────────
        summary = self._build_summary_comment(ranked, responses)

        # ── Audit log ───────────────────────────────────────────────────
        # Log aggregation decision
        logger.info(
            "Aggregator run %s: %d → %d findings (grouped into %d)",
            review_run_id,
            len(all_findings),
            len(merged_findings),
            len(dedup_groups),
        )

        return AggregatorOutput(
            review_run_id=review_run_id,
            pr=repo,
            ranked_findings=ranked,
            summary_comment_markdown=summary,
        )

    def _build_summary_comment(
        self,
        ranked: list[RankedFinding],
        responses: list[SpecialistResponse],
    ) -> str:
        """Build a GitHub-flavored markdown summary comment."""
        lines = ["## Verdity Review", ""]

        total = len(ranked)
        critical = sum(1 for r in ranked if r.finding.severity == Severity.CRITICAL)
        high = sum(1 for r in ranked if r.finding.severity == Severity.HIGH)
        medium = sum(1 for r in ranked if r.finding.severity == Severity.MEDIUM)
        low = sum(1 for r in ranked if r.finding.severity == Severity.LOW)
        info = sum(1 for r in ranked if r.finding.severity == Severity.INFO)

        lines.append(f"**{total} finding(s)** across {len(responses)} specialist(s)")
        if critical:
            lines.append(f"🔴 {critical} critical")
        if high:
            lines.append(f"🟠 {high} high")
        if medium:
            lines.append(f"🟡 {medium} medium")
        if low:
            lines.append(f"🔵 {low} low")
        if info:
            lines.append(f"⚪ {info} info")
        lines.append("")

        if ranked:
            lines.append("### Findings")
            lines.append("")
            for r in ranked[:10]:  # top 10
                f = r.finding
                emoji = {
                    "critical": "🔴",
                    "high": "🟠",
                    "medium": "🟡",
                    "low": "🔵",
                    "info": "⚪",
                }.get(f.severity.value, "⚪")
                emoji_str = f"- {emoji} **[{f.severity.value.upper()}]**"
                lines.append(f"{emoji_str} — {f.summary}")
                if f.explanation:
                    lines.append(f"  > {f.explanation}")
            if len(ranked) > 10:
                lines.append(f"\n_... and {len(ranked) - 10} more_")

        lines.append("")
        lines.append("---")
        lines.append("*Powered by [Verdity](https://github.com/verdity/verdity)*")
        return "\n".join(lines)
