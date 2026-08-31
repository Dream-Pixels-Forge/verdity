"""
Normalized internal event schema for Verdity.

All events emitted by the Ingestion Gateway conform to this schema.
Schema validation is enforced at both the publisher (gateway) and
consumer (orchestrator) sides to catch mismatches early.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, StrictStr, field_validator


# ── Trigger Types ────────────────────────────────────────────────────


class TriggerType(str, Enum):
    PR_OPENED = "pr.opened"
    PR_SYNCHRONIZE = "pr.synchronize"
    PR_REOPENED = "pr.reopened"
    PR_READY_FOR_REVIEW = "pr.ready_for_review"
    REVIEW_COMMENT_CREATED = "review_comment.created"
    CHECK_SUITE_REREQUESTED = "check_suite.rerequested"
    PUSH = "push"
    INSTALLATION_CREATED = "installation.created"
    INSTALLATION_REPOSITORIES_ADDED = "installation_repositories.added"
    INSTALLATION_DELETED = "installation.deleted"


# ── Repo ─────────────────────────────────────────────────────────────


class RepoRef(BaseModel):
    owner: StrictStr
    name: StrictStr
    id: int


# ── Pull Request (normalized subset) ──────────────────────────────────


class PullRequestRef(BaseModel):
    number: int
    head_sha: StrictStr
    base_sha: StrictStr
    draft: bool = False


# ── Main Event ───────────────────────────────────────────────────────


class VerdityEvent(BaseModel):
    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    delivery_id: StrictStr
    trigger_type: TriggerType
    repo: RepoRef
    pull_request: Optional[PullRequestRef] = None
    push_ref: Optional[StrictStr] = None
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("delivery_id")
    @classmethod
    def delivery_id_not_empty(cls, v: str) -> str:
        if not v or v.strip() == "":
            raise ValueError("delivery_id must be a non-empty GitHub delivery UUID")
        return v.strip()


# ── Queue Envelope ────────────────────────────────────────────────────


class QueueEnvelope(BaseModel):
    event: VerdityEvent
    enqueued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    retry_count: int = Field(default=0, ge=0)
    max_retries: int = Field(default=3, ge=1)


# ── Specialist Invocation Payload ─────────────────────────────────────


class ReviewPolicy(BaseModel):
    depth: str = "standard"
    timeout_seconds: int = 120
    budget_tokens: int = 40000


class SpecialistContext(BaseModel):
    """Bundle of context passed to every specialist agent."""

    review_run_id: uuid.UUID
    repo_owner: str
    repo_name: str
    base_sha: str
    head_sha: str
    diff_files: list[dict] = Field(default_factory=list)
    policy: ReviewPolicy = Field(default_factory=ReviewPolicy)


class SpecialistInvocation(BaseModel):
    review_run_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    specialist: str
    repo: RepoRef
    diff_ref: dict
    policy: ReviewPolicy
    tools_enabled: list[str]


# ── Specialist Finding ────────────────────────────────────────────────


class ConcernType(str, Enum):
    SECURITY = "security"
    CODE_QUALITY = "code_quality"
    TESTING = "testing"
    DOCUMENTATION = "documentation"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class EvidenceItem(BaseModel):
    tool: StrictStr
    result: Optional[StrictStr] = None
    query: Optional[StrictStr] = None


class Finding(BaseModel):
    finding_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    concern: ConcernType
    severity: Severity
    file: StrictStr
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    summary: StrictStr
    explanation: StrictStr
    suggested_fix_diff: Optional[StrictStr] = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    agent_version: StrictStr
    prompt_hash: StrictStr


class SpecialistResponse(BaseModel):
    review_run_id: uuid.UUID
    specialist: str
    status: str
    findings: list[Finding] = Field(default_factory=list)
    tokens_used: dict[str, int] = Field(default_factory=dict)
    cost_usd: float = Field(default=0.0, ge=0)
    error: Optional[StrictStr] = None


# ── Aggregator Output ─────────────────────────────────────────────────


class RankedFinding(BaseModel):
    finding: Finding
    dedup_group_id: Optional[uuid.UUID] = None
    rank_score: float


class AggregatorOutput(BaseModel):
    review_run_id: uuid.UUID
    pr: RepoRef
    ranked_findings: list[RankedFinding]
    summary_comment_markdown: StrictStr
