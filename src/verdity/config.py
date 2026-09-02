"""
Package-level configuration loader.

Secrets are loaded from a managed secret store (KMS-backed).
In dev/staging, secrets are read from environment variables — never
from files checked into git.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Top-level Verdity settings. All secrets must come from env or a KMS."""

    # ── Ingestion Gateway ────────────────────────────────────────────
    # HMAC secret for GitHub webhook verification.
    # NEVER commit a value here; use a dev-only env var in non-prod.
    webhook_hmac_secret: SecretStr = Field(
        ..., description="HMAC-SHA256 secret for GitHub webhook verification"
    )
    webhook_hmac_secret_previous: SecretStr = Field(
        default="",
        description="Previous HMAC secret during rotation window (empty when not rotating)",
    )

    # ── GitHub App ───────────────────────────────────────────────────
    github_app_id: int = Field(..., description="GitHub App numeric ID")
    github_app_installation_id: str = Field(
        ..., description="Installation ID for the org/repo target"
    )
    github_app_private_key: SecretStr = Field(..., description="PEM private key for the GitHub App")

    # ── Event Queue ──────────────────────────────────────────────────
    # Production: redis:// or similar. Dev: sqlite-backed queue.
    queue_backend: Literal["redis", "sqlite"] = "sqlite"
    queue_redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL for production queue",
    )
    queue_sqlite_path: str = Field(
        default=":memory:", description="SQLite path for in-memory dev queue"
    )

    # ── Audit Store ──────────────────────────────────────────────────
    audit_sqlite_path: str = Field(
        default="audit_store.db", description="SQLite path for the append-only audit store"
    )

    # ── Token Economics ──────────────────────────────────────────────
    token_economics_enabled: bool = True
    # Per-repo monthly budget cap in USD (0 = unlimited)
    default_repo_budget_usd: float = Field(default=0.0, ge=0)
    # Per-org monthly budget cap in USD (0 = unlimited)
    default_org_budget_usd: float = Field(default=0.0, ge=0)

    # ── Semantic Index (Phase 2) ─────────────────────────────────────
    semantic_index_postgres_url: str = Field(
        default="", description="Postgres+pgvector URL for the shared semantic index"
    )

    # ── Secrets Rotation Grace Period ────────────────────────────────
    hmac_secret_rotation_grace_hours: int = Field(
        default=24, ge=0, description="Hours after which 'previous' secret triggers alert"
    )

    # ── PR Size Heuristic ────────────────────────────────────────────
    # PRs with total diff lines (additions + deletions) below this threshold
    # are treated as "lite" review (fast, style + obvious bugs only).
    small_pr_diff_threshold: int = Field(
        default=100,
        ge=1,
        description="Total diff lines (additions+deletions) below which the PR is considered small/lite",
    )
    # PRs with total diff lines (additions + deletions) above this threshold
    # are treated as "large" (extended review).
    large_pr_diff_threshold: int = Field(
        default=500,
        ge=1,
        description="Total diff lines (additions+deletions) above which the PR is considered large",
    )

    # ── LLM Integration (Phase 12) ───────────────────────────────────
    # LLM calls are optional — every agent works WITHOUT an LLM (constraint #10).
    # LLM is an enhancement for deeper analysis, not a requirement.
    llm_enabled: bool = Field(
        default=False,
        description="Enable LLM-enhanced analysis in specialist agents (default: off)",
    )
    llm_model: str = Field(
        default="gpt-4o-mini",
        description="Default LLM model for general analysis",
    )
    llm_security_model: str = Field(
        default="gpt-4o",
        description="LLM model for security analysis (requires higher capability)",
    )
    llm_temperature: float = Field(
        default=0.0,
        ge=0.0,
        le=2.0,
        description="LLM temperature — 0.0 for determinism (constraint #5)",
    )
    llm_max_tokens: int = Field(
        default=4096,
        ge=256,
        description="Maximum tokens in LLM response",
    )

    # ── Multi-Platform Webhook Support (Phase 13) ─────────────────────
    default_platform: str = Field(
        default="github",
        description="Default code hosting platform (github, gitlab, bitbucket)",
    )
    gitlab_webhook_secret: SecretStr = Field(
        default="",
        description="GitLab webhook verification token (X-Gitlab-Token)",
    )
    bitbucket_webhook_secret: SecretStr = Field(
        default="",
        description="Bitbucket webhook HMAC-SHA256 secret",
    )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance. Cached so environment is read once."""
    return Settings()


InspectorConfig = Settings
