"""
Platform abstraction — common interface for GitHub, GitLab, Bitbucket.

Each platform implements:
  - verify_webhook: platform-native HMAC/token verification
  - normalize_event: convert platform payload to Verdity's internal WebhookEvent
  - post_comment: post a review comment on a PR/MR
  - post_inline_comment: post an inline (line-level) comment
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Platform(ABC):
    """Abstract base for all supported code hosting platforms."""

    PLATFORM_NAME: str = "base"

    @abstractmethod
    def verify_webhook(
        self,
        headers: dict[str, str],
        body: bytes,
        secret: str,
    ) -> bool:
        """
        Verify the webhook signature using platform-native verification.

        Args:
            headers: HTTP request headers (case-insensitive dict)
            body: Raw request body bytes
            secret: Shared secret for this platform

        Returns:
            True if signature is valid, False otherwise.
        """
        ...

    @abstractmethod
    def normalize_event(
        self,
        headers: dict[str, str],
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Convert a platform-specific webhook payload to a Verdity internal event dict.

        The returned dict should match the structure expected by
        `verdity.webhook_normalizer.normalize_webhook()` or be directly
        consumable by the orchestrator.

        Returns:
            Dict with keys: trigger_type, repo, pull_request, action, delivery_id
        """
        ...

    @abstractmethod
    async def post_comment(
        self,
        *,
        owner: str,
        repo: str,
        number: int,
        body: str,
    ) -> dict[str, Any]:
        """
        Post a review comment on a PR/MR.

        Returns:
            Platform API response dict.
        """
        ...

    @abstractmethod
    async def post_inline_comment(
        self,
        *,
        owner: str,
        repo: str,
        number: int,
        commit_sha: str,
        file_path: str,
        line: int,
        body: str,
    ) -> dict[str, Any]:
        """
        Post an inline (line-level) comment on a PR/MR.

        Returns:
            Platform API response dict.
        """
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} platform={self.PLATFORM_NAME}>"
