"""
GitLab Platform — webhook verification, MR event normalization, and MR commenting.

GitLab webhook verification uses a shared secret token sent in X-Gitlab-Token header.
Unlike GitHub's HMAC, GitLab compares the token directly (constant-time).
"""

from __future__ import annotations

import base64
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


class GitLabPlatform(Platform):
    """
    GitLab platform implementation.

    Verification: Shared secret token in X-Gitlab-Token header.
    Event normalization: Converts GitLab MR webhook payloads to Verdity internal format.
    """

    PLATFORM_NAME = "gitlab"

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
        Verify GitLab webhook token.

        GitLab sends the token in X-Gitlab-Token header.
        Verification is a constant-time comparison of the raw token.
        """
        token = headers.get("x-gitlab-token", "")
        if not token or not secret:
            return False

        return hmac.compare_digest(token, secret)

    def normalize_event(
        self,
        headers: dict[str, str],
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Convert GitLab MR webhook payload to Verdity internal event dict.

        GitLab sends object_kind="merge_request" for MR events.
        The action attribute maps to Verdity trigger types.
        """
        object_kind = body.get("object_kind", "")
        delivery_id = headers.get("x-gitlab-event-uuid", "")

        # Only handle merge_request events
        if object_kind != "merge_request":
            return {
                "trigger_type": "unknown",
                "action": object_kind,
                "delivery_id": delivery_id,
                "repo": body.get("project", {}),
            }

        attrs = body.get("object_attributes", {})
        project = body.get("project", {})
        author = attrs.get("author", {})

        # Map GitLab action to Verdity trigger type
        trigger_map = {
            "open": "pr.opened",
            "update": "pr.synchronize",
            "reopen": "pr.reopened",
            "close": "pr.closed",
            "merge": "pr.merged",
        }
        action = attrs.get("action", "")
        trigger_type = trigger_map.get(action, "unknown")

        return {
            "trigger_type": trigger_type,
            "action": action,
            "delivery_id": delivery_id,
            "repo": {
                "owner": project.get("namespace", ""),
                "name": project.get("name", ""),
            },
            "pull_request": {
                "number": attrs.get("iid", 0),
                "head_sha": attrs.get("head_commit_sha", ""),
                "base_sha": attrs.get("target_commit_sha", ""),
                "title": attrs.get("title", ""),
                "body": attrs.get("description", ""),
                "author": author.get("username", ""),
                "diff_url": attrs.get("diff_refs", {}).get("base", {}).get("url", "")
                if isinstance(attrs.get("diff_refs"), dict)
                else "",
            },
        }

    async def handle_webhook(self, request: Request) -> VerdityEvent:
        """
        Handle incoming GitLab webhook: verify signature and normalize to VerdityEvent.

        This is the main entry point for the unified webhook endpoint.
        """
        raw_body = await request.body()
        headers_dict = {k.lower(): v for k, v in request.headers.items()}

        # Get secret from settings
        settings = get_settings()
        secret = settings.gitlab_webhook_secret.get_secret_value()

        if not secret:
            logger.warning("No GitLab webhook secret configured")
            raise HTTPException(status_code=401, detail="No secret configured for gitlab")

        if not self.verify_webhook(headers_dict, raw_body, secret):
            delivery_id = headers_dict.get("x-gitlab-event-uuid", "unknown")
            logger.warning("GitLab webhook verification failed for delivery=%s", delivery_id)
            raise HTTPException(status_code=401, detail="Invalid or missing signature")

        # Parse and normalize
        import json

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse GitLab webhook JSON: %s", exc)
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
        Post a note (comment) on a GitLab MR.

        Uses the GitLab API: POST /projects/:id/merge_requests/:mr_iid/notes
        """
        project_id = f"{owner}/{repo}"
        url = f"https://gitlab.com/api/v4/projects/{project_id}/merge_requests/{number}/notes"
        async with self._get_client() as client:
            resp = await client.post(
                url,
                json={"body": body},
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
        Post an inline discussion on a GitLab MR.

        Uses the GitLab API: POST /projects/:id/merge_requests/:mr_iid/discussions
        """
        project_id = f"{owner}/{repo}"
        url = f"https://gitlab.com/api/v4/projects/{project_id}/merge_requests/{number}/discussions"
        payload = {
            "body": body,
            "position": {
                "position_type": "text",
                "base_sha": commit_sha,
                "head_sha": commit_sha,
                "start_sha": commit_sha,
                "new_path": file_path,
                "new_line": line,
            },
        }
        async with self._get_client() as client:
            resp = await client.post(
                url,
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_merge_request(
        self,
        project_id: str,
        mr_iid: int,
    ) -> dict[str, Any]:
        """
        Fetch merge request details from GitLab API.

        Args:
            project_id: GitLab project ID (e.g., "owner/repo" or numeric ID)
            mr_iid: Merge request IID (internal ID)

        Returns:
            MR details as dict
        """
        url = f"https://gitlab.com/api/v4/projects/{project_id}/merge_requests/{mr_iid}"
        async with self._get_client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()

    async def get_diff(
        self,
        project_id: str,
        mr_iid: int,
    ) -> str:
        """
        Fetch merge request diff from GitLab API.

        Args:
            project_id: GitLab project ID
            mr_iid: Merge request IID

        Returns:
            Diff as unified diff string
        """
        url = f"https://gitlab.com/api/v4/projects/{project_id}/merge_requests/{mr_iid}/diffs"
        async with self._get_client() as client:
            resp = await client.get(url)
            resp.raise_for_status()
            diffs = resp.json()
            # Combine all diffs into a single unified diff string
            diff_parts = []
            for diff in diffs:
                diff_parts.append(diff.get("diff", ""))
            return "\n".join(diff_parts)

    async def get_file_content(
        self,
        project_id: str,
        file_path: str,
        ref: str,
    ) -> str:
        """
        Fetch file content from GitLab repository at a specific ref.

        Args:
            project_id: GitLab project ID
            file_path: Path to the file
            ref: Git ref (branch, tag, or commit SHA)

        Returns:
            Decoded file content as string
        """
        import urllib.parse

        encoded_path = urllib.parse.quote(file_path, safe="")
        url = f"https://gitlab.com/api/v4/projects/{project_id}/repository/files/{encoded_path}"
        params = {"ref": ref}
        async with self._get_client() as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            file_data = resp.json()
            content = file_data.get("content", "")
            encoding = file_data.get("encoding", "base64")
            if encoding == "base64":
                return base64.b64decode(content).decode("utf-8")
            return content
