"""
GitHub Platform — webhook verification, event normalization, and PR commenting.

Refactored from github_client.py. The original module is kept as a thin wrapper
for backward compatibility.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import httpx

from verdity.platforms.base import Platform

logger = logging.getLogger(__name__)


class GitHubPlatform(Platform):
    """
    GitHub platform implementation.

    Verification: HMAC-SHA256 via X-Hub-Signature-256 header.
    Event normalization: Converts GitHub webhook payloads to Verdity internal format.
    """

    PLATFORM_NAME = "github"

    def verify_webhook(
        self,
        headers: dict[str, str],
        body: bytes,
        secret: str,
    ) -> bool:
        """
        Verify GitHub webhook signature (HMAC-SHA256).

        GitHub sends the signature in X-Hub-Signature-256 as "sha256=<hex>".
        """
        signature_header = headers.get("x-hub-signature-256", "")
        if not signature_header:
            return False

        expected = "sha256=" + hmac.new(
            secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(expected, signature_header)

    def normalize_event(
        self,
        headers: dict[str, str],
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Convert GitHub webhook payload to Verdity internal event dict.

        Maps GitHub PR events (opened, synchronize, reopened) to trigger types.
        """
        event_name = headers.get("x-github-event", "")
        action = body.get("action", "")
        delivery_id = headers.get("x-github-delivery", "")

        # Only handle pull_request events
        if event_name != "pull_request":
            return {
                "trigger_type": "unknown",
                "action": event_name,
                "delivery_id": delivery_id,
                "repo": body.get("repository", {}),
            }

        pr_data = body.get("pull_request", {})
        repo_data = body.get("repository", {})

        # Map GitHub action to Verdity trigger type
        trigger_map = {
            "opened": "pr.opened",
            "synchronize": "pr.synchronize",
            "reopened": "pr.reopened",
            "closed": "pr.closed",
        }
        trigger_type = trigger_map.get(action, "unknown")

        return {
            "trigger_type": trigger_type,
            "action": action,
            "delivery_id": delivery_id,
            "repo": {
                "owner": repo_data.get("owner", {}).get("login", ""),
                "name": repo_data.get("name", ""),
            },
            "pull_request": {
                "number": pr_data.get("number", 0),
                "head_sha": pr_data.get("head", {}).get("sha", ""),
                "base_sha": pr_data.get("base", {}).get("sha", ""),
                "title": pr_data.get("title", ""),
                "body": pr_data.get("body", ""),
                "author": pr_data.get("user", {}).get("login", ""),
                "diff_url": pr_data.get("diff_url", ""),
            },
        }

    async def post_comment(
        self,
        *,
        owner: str,
        repo: str,
        number: int,
        body: str,
    ) -> dict[str, Any]:
        """Post a review comment on a GitHub PR."""
        url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}/comments"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                json={"body": body},
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            resp.raise_for_status()
            return resp.json()

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
        """Post an inline comment on a GitHub PR review."""
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}/reviews"
        payload = {
            "commit_id": commit_sha,
            "body": body,
            "event": "COMMENT",
            "comments": [
                {
                    "path": file_path,
                    "position": line,
                    "body": body,
                }
            ],
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            resp.raise_for_status()
            return resp.json()
