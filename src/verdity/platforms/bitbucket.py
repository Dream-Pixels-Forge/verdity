"""
Bitbucket Platform — webhook verification, PR event normalization, and PR commenting.

Bitbucket webhook verification uses HMAC-SHA256 with the shared secret.
The signature is sent in the X-Hub-Signature header (not X-Hub-Signature-256).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

import httpx

from verdity.platforms.base import Platform

logger = logging.getLogger(__name__)


class BitbucketPlatform(Platform):
    """
    Bitbucket platform implementation.

    Verification: HMAC-SHA256 via X-Hub-Signature header.
    Event normalization: Converts Bitbucket PR webhook payloads to Verdity internal format.
    """

    PLATFORM_NAME = "bitbucket"

    def verify_webhook(
        self,
        headers: dict[str, str],
        body: bytes,
        secret: str,
    ) -> bool:
        """
        Verify Bitbucket webhook signature (HMAC-SHA256).

        Bitbucket sends the signature in X-Hub-Signature as "sha256=<hex>".
        """
        signature_header = headers.get("x-hub-signature", "")
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
        Convert Bitbucket PR webhook payload to Verdity internal event dict.

        Bitbucket sends event type in the X-Event-Key header.
        PR events have keys like "pullrequest:created", "pullrequest:updated".
        """
        event_key = headers.get("x-event-key", "")
        delivery_id = headers.get("x-hook-uuid", "")

        # Only handle pull request events
        if not event_key.startswith("pullrequest:"):
            return {
                "trigger_type": "unknown",
                "action": event_key,
                "delivery_id": delivery_id,
                "repo": body.get("repository", {}),
            }

        pr_data = body.get("pullrequest", {})
        repo_data = body.get("repository", {})
        owner_data = repo_data.get("owner", {})

        # Map Bitbucket event key to Verdity trigger type
        action = event_key.split(":", 1)[1] if ":" in event_key else ""
        trigger_map = {
            "created": "pr.opened",
            "updated": "pr.synchronize",
            "approved": "pr.approved",
            "merged": "pr.merged",
            "declined": "pr.closed",
        }
        trigger_type = trigger_map.get(action, "unknown")

        # Extract source and target branches
        source = pr_data.get("source", {})
        destination = pr_data.get("destination", {})

        return {
            "trigger_type": trigger_type,
            "action": action,
            "delivery_id": delivery_id,
            "repo": {
                "owner": owner_data.get("uuid", owner_data.get("username", "")),
                "name": repo_data.get("name", ""),
            },
            "pull_request": {
                "number": pr_data.get("id", 0),
                "head_sha": source.get("commit", {}).get("hash", ""),
                "base_sha": destination.get("commit", {}).get("hash", ""),
                "title": pr_data.get("title", ""),
                "body": pr_data.get("description", ""),
                "author": pr_data.get("author", {}).get("username", ""),
                "diff_url": pr_data.get("links", {}).get("diff", {}).get("href", ""),
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
        """
        Post a comment on a Bitbucket PR.

        Uses the Bitbucket API: POST /2.0/repositories/{workspace}/{repo_slug}/pullrequests/{number}/comments
        """
        url = (
            f"https://api.bitbucket.org/2.0/repositories/{owner}/{repo}"
            f"/pullrequests/{number}/comments"
        )
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                json={"content": {"raw": body}},
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
        """
        Post an inline comment on a Bitbucket PR.

        Uses the Bitbucket API with inline parameter.
        """
        url = (
            f"https://api.bitbucket.org/2.0/repositories/{owner}/{repo}"
            f"/pullrequests/{number}/comments"
        )
        payload = {
            "content": {"raw": body},
            "inline": {
                "path": file_path,
                "to": line,
            },
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
