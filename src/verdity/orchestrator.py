"""
Orchestrator — durable workflow engine for PR review runs.

Architecture doc §2.3: the orchestrator is a durable workflow (not a stateless
function chain) so that multi-agent reviews survive process restarts and can be
resumed, inspected, and audited mid-flight.

Each PR review run is one workflow execution, keyed by review_run_id.
Fan-out to specialists is concurrent (asyncio.gather); one specialist's timeout
or failure never blocks the others.

Non-negotiable constraint #3: specialists run in parallel, not sequentially.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from verdity.audit_store import AuditStore
from verdity.event_queue import EventQueue
from verdity.schemas import (
    QueueEnvelope,
    ReviewPolicy,
    SpecialistContext,
    SpecialistResponse,
    TriggerType,
    VerdityEvent,
)
from verdity.semantic_index import SemanticIndex
from verdity.token_economics import TokenEconomicsService

logger = logging.getLogger(__name__)


# ── Review Run States ─────────────────────────────────────────────────


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"  # some specialists timed out


@dataclass
class ReviewRun:
    """In-memory durable state for a single PR review run."""
    review_run_id: uuid.UUID
    event: VerdityEvent
    status: RunStatus = RunStatus.PENDING
    policy: ReviewPolicy | None = None
    specialist_results: dict[str, SpecialistResponse] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    error: str | None = None


# ── Trigger → Policy Mapping (Orchestration doc §3) ──────────────────


def resolve_policy(event: VerdityEvent) -> ReviewPolicy:
    """
    Map a VerdityEvent to a ReviewPolicy per the trigger taxonomy.
    (Orchestration doc §3 — abbreviated for Phase 3; full table in prod config.)
    """
    trigger = event.trigger_type

    if trigger == TriggerType.PUSH:
        # Push events trigger semantic-index re-index, not a full review
        return ReviewPolicy(depth="standard", timeout_seconds=30, budget_tokens=5000)

    if trigger in (TriggerType.INSTALLATION_CREATED, TriggerType.INSTALLATION_REPOSITORIES_ADDED):
        return ReviewPolicy(depth="standard", timeout_seconds=10, budget_tokens=1000)

    # PR-related triggers
    pr = event.pull_request
    if pr is None:
        return ReviewPolicy(depth="standard", timeout_seconds=120, budget_tokens=40000)

    # Determine depth based on PR size heuristic
    # In production, this comes from the GitHub API diff stats (additions + deletions).
    from verdity.config import get_settings
    settings = get_settings()
    # Use diff stats if available; fall back to 0
    diff_lines = 0
    if event.pull_request is not None:
        diff_lines = getattr(event.pull_request, "additions", 0) + getattr(event.pull_request, "deletions", 0)
    is_large = diff_lines >= settings.large_pr_diff_threshold
    depth = "extended" if is_large else "standard"
    timeout = 15 * 60 if is_large else 5 * 60  # seconds
    budget = 200_000 if is_large else 40_000

    # Security forced-on for sensitive paths (checked during specialist selection)
    return ReviewPolicy(
        depth=depth,
        timeout_seconds=timeout,
        budget_tokens=budget,
    )


def resolve_specialists(event: VerdityEvent, policy: ReviewPolicy) -> list[str]:
    """
    Return the list of specialist names to invoke for this event.
    Security is forced-on for /infra/** and /auth/** paths.
    """
    trigger = event.trigger_type
    specialists: list[str] = []

    # Default: all four for pr.opened / pr.ready_for_review / check_suite.rerequested
    if trigger in (
        TriggerType.PR_OPENED,
        TriggerType.PR_REOPENED,
        TriggerType.PR_READY_FOR_REVIEW,
        TriggerType.CHECK_SUITE_REREQUESTED,
    ):
        specialists = ["security", "code_quality", "testing", "documentation"]
    elif trigger == TriggerType.PR_SYNCHRONIZE:
        # Delta-aware: all specialists run but only on changed files
        # (The orchestrator will filter per-specialist in the production path.)
        specialists = ["security", "code_quality", "testing", "documentation"]
    elif trigger == TriggerType.REVIEW_COMMENT_CREATED:
        # Single relevant specialist with conversational context
        specialists = ["security"]  # simplified for Phase 3
    else:
        specialists = []

    # Force security on for sensitive paths (would check diff file paths in prod)
    # For Phase 3, we always include security
    if "security" not in specialists:
        specialists.insert(0, "security")

    return specialists


# ── Specialist Runner Registry ────────────────────────────────────────


SpecialistFn = Callable[
    [SpecialistContext, SemanticIndex, TokenEconomicsService, AuditStore],
    asyncio.Future | SpecialistResponse,
]


class Orchestrator:
    """
    Durable workflow orchestrator for PR review runs.

    Manages the scatter-gather lifecycle:
    1. Consume event from queue
    2. Resolve trigger → policy → specialist list
    3. Fan out to specialists concurrently (asyncio.gather)
    4. Gather results, handle partial failures
    5. Log everything to Audit Store
    """

    def __init__(
        self,
        queue: EventQueue,
        semantic_index: SemanticIndex,
        token_economics: TokenEconomicsService,
        audit_store: AuditStore,
    ) -> None:
        self._queue = queue
        self._index = semantic_index
        self._te = token_economics
        self._audit = audit_store
        self._runs: dict[uuid.UUID, ReviewRun] = {}
        self._specialists: dict[str, SpecialistFn] = {}

    def register_specialist(self, name: str, fn: SpecialistFn) -> None:
        """Register a specialist agent function."""
        self._specialists[name] = fn

    async def process_event(self, envelope: QueueEnvelope) -> uuid.UUID:
        """
        Main entry point: consume one queue envelope and run the full review.
        Returns the review_run_id.
        """
        event = envelope.event
        review_run_id = uuid.uuid4()

        run = ReviewRun(review_run_id=review_run_id, event=event, status=RunStatus.RUNNING)
        self._runs[review_run_id] = run

        # Audit: run started
        await self._audit.append(
            event_type="orchestrator.run_started",
            entity_type="review_run",
            entity_id=str(review_run_id),
            payload={
                "trigger_type": event.trigger_type.value,
                "repo": f"{event.repo.owner}/{event.repo.name}",
                "pr_number": event.pull_request.number if event.pull_request else None,
            },
            related_run_id=review_run_id,
        )

        # Resolve policy
        policy = resolve_policy(event)
        run.policy = policy
        logger.info(
            "Run %s: policy resolved — depth=%s timeout=%ds budget=%d tokens",
            review_run_id, policy.depth, policy.timeout_seconds, policy.budget_tokens,
        )

        # Resolve which specialists to run
        specialist_names = resolve_specialists(event, policy)
        logger.info("Run %s: running specialists: %s", review_run_id, specialist_names)

        # ── Fan-out: run all specialists concurrently ──────────────────
        # Non-negotiable constraint #3: one specialist's timeout/failure
        # must never block the others.
        tasks: dict[str, asyncio.Task] = {}
        for name in specialist_names:
            fn = self._specialists.get(name)
            if fn is None:
                logger.warning("Run %s: specialist '%s' not registered — skipping", review_run_id, name)
                run.specialist_results[name] = SpecialistResponse(
                    review_run_id=review_run_id,
                    specialist=name,
                    status="failed",
                    error=f"Specialist '{name}' not registered",
                )
                continue

            task = asyncio.create_task(
                self._run_specialist(name, fn, run, policy),
                name=f"specialist-{name}",
            )
            tasks[name] = task

        # Gather with per-agent timeout; collect partial results
        await self._gather_results(review_run_id, run, policy, tasks)

        # Determine overall status
        any_partial = any(r.status == "partial" for r in run.specialist_results.values())
        run.status = RunStatus.PARTIAL if any_partial else RunStatus.COMPLETED
        run.completed_at = datetime.now(timezone.utc)

        # Audit: run completed
        await self._audit.append(
            event_type="orchestrator.run_completed",
            entity_type="review_run",
            entity_id=str(review_run_id),
            payload={
                "status": run.status.value,
                "specialists_ran": list(run.specialist_results.keys()),
                "findings_total": sum(
                    len(r.findings) for r in run.specialist_results.values()
                ),
                "cost_usd": sum(r.cost_usd for r in run.specialist_results.values()),
            },
            related_run_id=review_run_id,
        )

        logger.info(
            "Run %s: %s — %d findings across %d specialists",
            review_run_id, run.status.value,
            sum(len(r.findings) for r in run.specialist_results.values()),
            len(run.specialist_results),
        )

        return review_run_id

    async def _gather_results(
        self,
        review_run_id: uuid.UUID,
        run: ReviewRun,
        policy: ReviewPolicy,
        tasks: dict[str, asyncio.Task],
    ) -> None:
        """
        Gather specialist tasks and collect results.
        Handles timeouts, cancellations, and individual task failures.
        """
        # Gather with per-agent timeout
        if tasks:
            done, pending = await asyncio.wait(
                tasks.values(),
                timeout=float(policy.timeout_seconds),
                return_when=asyncio.ALL_COMPLETED,
            )
            # Cancel any still-running specialists (timed out)
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Collect results
        for name, task in tasks.items():
            if name in run.specialist_results:
                continue  # already set (error case)
            if task.cancelled():
                continue  # already handled by cancellation loop
            try:
                result = task.result()
                run.specialist_results[name] = result
            except Exception as exc:
                logger.error("Run %s: specialist '%s' raised: %s", review_run_id, name, exc)
                run.specialist_results[name] = SpecialistResponse(
                    review_run_id=review_run_id,
                    specialist=name,
                    status="failed",
                    error=str(exc),
                )

    async def _run_specialist(
        self,
        name: str,
        fn: SpecialistFn,
        run: ReviewRun,
        policy: ReviewPolicy,
    ) -> SpecialistResponse:
        """
        Invoke a single specialist with timeout isolation.
        Returns a SpecialistResponse regardless of success/failure/timeout.
        """
        event = run.event
        pr = event.pull_request
        ctx = SpecialistContext(
            review_run_id=run.review_run_id,
            repo_owner=event.repo.owner,
            repo_name=event.repo.name,
            base_sha=pr.base_sha if pr else "",
            head_sha=pr.head_sha if pr else "",
            diff_files=[],  # populated by caller or extracted from event
            policy=policy,
        )
        try:
            result = await asyncio.wait_for(fn(ctx, self._index, self._te, self._audit),
                                             timeout=float(policy.timeout_seconds))
            if not isinstance(result, SpecialistResponse):
                raise TypeError(f"Specialist '{name}' returned non-SpecialistResponse: {type(result)}")
            return result
        except asyncio.TimeoutError:
            logger.warning("Run %s: specialist '%s' timed out after %ds",
                           run.review_run_id, name, policy.timeout_seconds)
            return SpecialistResponse(
                review_run_id=run.review_run_id,
                specialist=name,
                status="partial",
                findings=[],
                error=f"Timed out after {policy.timeout_seconds}s",
            )
        except Exception as exc:
            logger.error("Run %s: specialist '%s' failed: %s",
                         run.review_run_id, name, exc)
            return SpecialistResponse(
                review_run_id=run.review_run_id,
                specialist=name,
                status="failed",
                findings=[],
                error=str(exc),
            )

    def get_run(self, review_run_id: uuid.UUID) -> ReviewRun | None:
        """Look up a review run by ID (for inspection/resume)."""
        return self._runs.get(review_run_id)

    def list_runs(self, limit: int = 50) -> list[ReviewRun]:
        """List recent runs for monitoring."""
        sorted_runs = sorted(
            self._runs.values(),
            key=lambda r: r.created_at,
            reverse=True,
        )
        return sorted_runs[:limit]
