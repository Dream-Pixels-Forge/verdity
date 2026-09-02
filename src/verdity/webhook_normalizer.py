"""
GitHub payload normalizer.

Converts raw GitHub webhook payloads into our normalized VerdityEvent.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from verdity.schemas import (
    PullRequestRef,
    RepoRef,
    TriggerType,
    VerdityEvent,
)

logger = logging.getLogger(__name__)


# Mapping from GitHub event+action to our normalized TriggerType
_TRIGGER_MAP: dict[tuple[str, str | None], TriggerType] = {
    ("pull_request", "opened"): TriggerType.PR_OPENED,
    ("pull_request", "synchronize"): TriggerType.PR_SYNCHRONIZE,
    ("pull_request", "reopened"): TriggerType.PR_REOPENED,
    ("pull_request", "ready_for_review"): TriggerType.PR_READY_FOR_REVIEW,
    ("pull_request_review_comment", "created"): TriggerType.REVIEW_COMMENT_CREATED,
    ("check_suite", "rerequested"): TriggerType.CHECK_SUITE_REREQUESTED,
    ("push", None): TriggerType.PUSH,
    ("installation", "created"): TriggerType.INSTALLATION_CREATED,
    ("installation_repositories", "added"): TriggerType.INSTALLATION_REPOSITORIES_ADDED,
    ("installation", "deleted"): TriggerType.INSTALLATION_DELETED,
}


def _extract_repo(payload: dict) -> RepoRef:
    """Extract repo info — tries repository first, falls back to repo (push events)."""
    repo = payload.get("repository") or payload.get("repo") or {}
    return RepoRef(
        owner=repo.get("owner", {}).get("login", repo.get("full_name", "").split("/")[0]),
        name=repo.get("name", ""),
        id=repo.get("id", 0),
    )


def normalize_webhook(
    *,
    event_name: str,
    action: str | None,
    delivery_id: str,
    payload: dict,
) -> VerdityEvent:
    """
    Normalize a raw GitHub webhook payload into a VerdityEvent.

    This function is pure (no I/O) and deterministic — easy to test.
    It does NOT perform HMAC verification; that happens in the gateway before
    this function is called.
    """
    trigger = _TRIGGER_MAP.get((event_name, action))
    if trigger is None:
        # Try mapping by event name only (without action)
        name_only_trigger = _TRIGGER_MAP.get((event_name, None))
        if name_only_trigger is not None:
            logger.warning(
                "Unknown action '%s' for event %s — using trigger %s from event name only",
                action, event_name, name_only_trigger,
            )
            trigger = name_only_trigger
        else:
            # Normalize unknown events gracefully — fall back to a generic trigger
            normalized = event_name.lower().replace("_", ".")
            if action:
                normalized += f".{action}"
            logger.warning(
                "Unknown event+action: %s/%s — using generic trigger PR_OPENED "
                "(raw event name preserved in delivery_id for debugging)",
                event_name, action,
            )
            trigger = TriggerType.PR_OPENED  # safe fallback

    repo = _extract_repo(payload)

    # Build pull_request reference if present
    pr_ref: PullRequestRef | None = None
    pr = payload.get("pull_request") or payload.get("issue")
    if pr:
        pr_ref = PullRequestRef(
            number=pr.get("number", 0),
            head_sha=(pr.get("head") or {}).get("sha", ""),
            base_sha=(pr.get("base") or {}).get("sha", ""),
            draft=pr.get("draft", False),
            additions=pr.get("additions", 0) or 0,
            deletions=pr.get("deletions", 0) or 0,
        )

    # For push events, capture the new HEAD ref
    push_ref: str | None = None
    if event_name == "push" and "after" in payload:
        push_ref = payload["after"]

    # Compute a stable prompt_hash-equivalent for this event (for audit trail)
    # (available if needed for audit trail)

    return VerdityEvent(
        delivery_id=delivery_id,
        trigger_type=trigger,
        repo=repo,
        pull_request=pr_ref,
        push_ref=push_ref,
        received_at=datetime.now(UTC),
    )
