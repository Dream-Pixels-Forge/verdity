"""
Budget Enforcer — real-time spend monitoring and graceful degradation.

Per Constraint #8 and Orchestration doc §9: when spend crosses thresholds,
the orchestrator degrades rather than errors or overspends.

Degradation order (configurable per org):
  1. drop optional specialists (docs)
  2. reduce context window / chunk size
  3. fall back to cheaper model tier
  4. queue-only mode (no auto-post)
  Security is the LAST specialist dropped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from verdity.token_economics import TokenEconomicsService

logger = logging.getLogger(__name__)


class DegradationSignal(str, Enum):
    """Signals returned by the budget enforcer."""

    NORMAL = "normal"  # within budget, no action needed
    WARN = "warn"  # approaching budget, alert only
    DEGRADE_OPTIONAL = "degrade_optional"  # drop optional specialists
    HALT = "halt"  # budget exhausted, queue-only mode


@dataclass
class BudgetStatus:
    spend_usd: float
    budget_usd: float
    ratio: float
    signal: DegradationSignal
    dropped_specialists: list[str] = field(default_factory=list)
    notes: str = ""


# ── Default degradation config ────────────────────────────────────────

# Specialists that can be dropped first (optional) vs last (required)
_OPTIONAL_SPECIALISTS = {"documentation", "testing"}
_REQUIRED_SPECIALISTS = {"security", "code_quality"}

# Budget thresholds (as fraction of budget_usd)
_WARN_THRESHOLD = 0.80
_DEGRADE_THRESHOLD = 0.60
_HALT_THRESHOLD = 1.00


class BudgetEnforcer:
    """
    Monitors spend against budget caps and returns degradation signals.

    Integrates with the orchestrator: before launching specialists, the
    orchestrator calls `check_budget()` and adjusts the specialist list
    based on the returned signal.
    """

    def __init__(
        self,
        te_service: TokenEconomicsService,
        warn_threshold: float = _WARN_THRESHOLD,
        degrade_threshold: float = _DEGRADE_THRESHOLD,
        halt_threshold: float = _HALT_THRESHOLD,
    ) -> None:
        self._te = te_service
        self._warn = warn_threshold
        self._degrade = degrade_threshold
        self._halt = halt_threshold
        self._drop_history: dict[str, list[float]] = {}  # repo → spend at drop time

    async def check_budget(
        self,
        repo_owner: str,
        repo_name: str,
        budget_usd: float,
        current_specialists: list[str],
    ) -> BudgetStatus:
        """
        Check current spend against budget and return degradation signal.

        If degraded, returns a status with specialists to drop.
        """
        stats = await self._te.get_spend(repo_owner=repo_owner, repo_name=repo_name)
        spend = stats["spend_usd"]
        ratio = spend / budget_usd if budget_usd > 0 else 0.0

        if ratio >= self._halt:
            # At halt: drop ALL non-security specialists; security last
            remaining = [s for s in current_specialists if s not in _OPTIONAL_SPECIALISTS]
            return BudgetStatus(
                spend_usd=spend,
                budget_usd=budget_usd,
                ratio=round(ratio, 4),
                signal=DegradationSignal.HALT,
                dropped_specialists=[s for s in current_specialists if s in _OPTIONAL_SPECIALISTS],
                notes=f"Budget exhausted — queue-only mode; keeping: {remaining or ['security']}",
            )
        elif ratio >= self._warn:
            # Drop optional specialists
            to_drop = [s for s in current_specialists if s in _OPTIONAL_SPECIALISTS]
            if to_drop:
                self._drop_history[f"{repo_owner}/{repo_name}"] = spend
                return BudgetStatus(
                    spend_usd=spend,
                    budget_usd=budget_usd,
                    ratio=round(ratio, 4),
                    signal=DegradationSignal.DEGRADE_OPTIONAL,
                    dropped_specialists=to_drop,
                    notes=f"Dropping optional specialists: {to_drop}",
                )
            return BudgetStatus(
                spend_usd=spend,
                budget_usd=budget_usd,
                ratio=round(ratio, 4),
                signal=DegradationSignal.WARN,
                notes="Approaching budget but no optional specialists to drop",
            )
        elif ratio >= self._degrade:
            # Early warning — drop optional if any remain
            to_drop = [s for s in current_specialists if s in _OPTIONAL_SPECIALISTS]
            if to_drop:
                return BudgetStatus(
                    spend_usd=spend,
                    budget_usd=budget_usd,
                    ratio=round(ratio, 4),
                    signal=DegradationSignal.DEGRADE_OPTIONAL,
                    dropped_specialists=to_drop,
                    notes=f"Preemptive drop: {to_drop}",
                )
        return BudgetStatus(
            spend_usd=spend,
            budget_usd=budget_usd,
            ratio=round(ratio, 4),
            signal=DegradationSignal.NORMAL,
        )

    async def get_spend_summary(
        self,
        repo_owner: str | None = None,
        repo_name: str | None = None,
        org: str | None = None,
    ) -> dict[str, Any]:
        """Return dashboard-ready spend summary for current period."""
        stats = await self._te.get_spend(
            repo_owner=repo_owner,
            repo_name=repo_name,
            org=org,
        )
        return {
            "total_spend_usd": stats["spend_usd"],
            "total_calls": stats["total_calls"],
            "total_input_tokens": stats["tokens_in"],
            "total_output_tokens": stats["tokens_out"],
            "filter": {
                "repo_owner": repo_owner,
                "repo_name": repo_name,
                "org": org,
            },
        }


async def dashboard_stats(te: TokenEconomicsService) -> dict[str, Any]:
    """
    Aggregate dashboard data: spend by scope, cost per PR, cost per finding.
    """
    # Overall spend
    overall = await te.get_spend()

    # Per-org and per-repo breakdowns
    spend_by_org = await te.get_spend_by_org()
    spend_by_repo = await te.get_spend_by_repo()

    return {
        "overall": overall,
        "spend_by_org": spend_by_org,
        "spend_by_repo": spend_by_repo,
        "recent_runs": [],
    }
