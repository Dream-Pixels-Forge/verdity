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
from fastapi import HTTPException, Request

from verdity.config import get_settings
from verdity.platforms.base import Platform
from verdity.schemas import VerdityEvent

logger = logging.getLogger(__name__)


# Default HTTPX timeout configuration (Issue #41)
def _get_default_timeout_total() -> float:
    return get_settings().http_timeout_total


def _get_default_timeout_connect() -> float:
    return get_settings().http_timeout_connect


class BitbucketPlatform(Platform):
    """
    Bitbucket platform implementation.

    Verification: HMAC-SHA256 via X-Hub-Signature header.
    Event normalization: Converts Bitbucket PR webhook payloads to Verdity internal format.
    """

    PLATFORM_NAME = "bitbucket"

    def __init__(
        self,
        *,
        timeout_total: float | None = None,
        timeout_connect: float | None = None,
    ) -> None:
        self._timeout_total = timeout_total or _get_default_timeout_total()
        self._timeout_connect = timeout_connect or _get_default_timeout_connect()

    def _get_client(self) -> httpx.AsyncClient:
        """Return an HTTP client with configured timeouts."""
        return httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=self._timeout_connect,
                read=self._timeout_total,
                write=self._timeout_total,
                pool=self._timeout_total,
            ),
        )

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

        expected = (
            "sha256="
            + hmac.new(
                secret.encode(),
                body,
                hashlib.sha256,
            ).hexdigest()
        )

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

    async def handle_webhook(self, request: Request) -> VerdityEvent:
        """
        Handle incoming Bitbucket webhook: verify signature and normalize to VerdityEvent.

        This is the main entry point for the unified webhook endpoint.
        """
        raw_body = await request.body()
        headers_dict = {k.lower(): v for k, v in request.headers.items()}

        # Get secret from settings
        settings = get_settings()
        secret = settings.bitbucket_webhook_secret.get_secret_value()

        if not secret:
            logger.warning("No Bitbucket webhook secret configured")
            raise HTTPException(status_code=401, detail="No secret configured for bitbucket")

        if not self.verify_webhook(headers_dict, raw_body, secret):
            delivery_id = headers_dict.get("x-hook-uuid", "unknown")
            logger.warning("Bitbucket webhook verification failed for delivery=%s", delivery_id)
            raise HTTPException(status_code=401, detail="Invalid or missing signature")

        # Parse and normalize
        import json
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse Bitbucket webhook JSON: %s", exc)
            raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

        event_dict = self.normalize_event(headers_dict, payload)

        # Convert to VerdityEvent
        from verdity.schemas import PullRequestRef, RepoRef, TriggerType

        trigger_type_str = event_dict.get("trigger_type", "unknown")
        try:
            trigger_type = TriggerType(trigger_type_str)
        except ValueError:
            trigger_type = TriggerType.PR_OPENED

        repo_dict = event_dict.get("repo", {})
        pr_dict = event_dict.get("pull_request", {})

        return VerdityEvent(
            delivery_id=event_dict.get("delivery_id", ""),
            trigger_type=trigger_type,
            repo=RepoRef(
                owner=repo_dict.get("owner", ""),
                name=repo_dict.get("name", ""),
                id=repo_dict.get("id", 0),
            ),
            pull_request=PullRequestRef(
                number=pr_dict.get("number", 0),
                head_sha=pr_dict.get("head_sha", ""),
                base_sha=pr_dict.get("base_sha", ""),
                title=pr_dict.get("title", ""),
                body=pr_dict.get("body", ""),
                author=pr_dict.get("author", ""),
                diff_url=pr_dict.get("diff_url", ""),
            ),
        )

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
        async with self._get_client() as client:
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
        async with self._get_client() as client:
            resp = await client.post(
                url,
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_pull_request(
        self,
        workspace: str,
        repo_slug: str,
        pr_id: int,
    ) -> dict[str, Any]:
        """
        Fetch pull request details from Bitbucket API.

        Args:
            workspace: Bitbucket workspace name
            repo_slug: Repository slug
            pr_id: Pull request ID

        Returns:
            PR details as dict
        """
        url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}"
        async with self._get_client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()

    async def get_diff(
        self,
        workspace: str,
        repo_slug: str,
        pr_id: int,
    ) -> str:
        """
        Fetch pull request diff from Bitbucket API.

        Args:
            workspace: Bitbucket workspace name
            repo_slug: Repository slug
            pr_id: Pull request ID

        Returns:
            Diff as unified diff string
        """
        url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_slug}/pullrequests/{pr_id}/diff"
        async with self._get_client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

    async def get_file_content(
        self,
        workspace: str,
        repo_slug: str,
        file_path: str,
        commit: str,
    ) -> str:
        """
        Fetch file content from Bitbucket repository at a specific commit.

        Args:
            workspace: Bitbucket workspace name
            repo_slug: Repository slug
            file_path: Path to the file
            commit: Commit hash

        Returns:
            File content as string
        """
        import urllib.parse
        encoded_path = urllib.parse.quote(file_path, safe="")
        url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_slug}/src/{commit}/{encoded_path}"
        async with self._get_client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
