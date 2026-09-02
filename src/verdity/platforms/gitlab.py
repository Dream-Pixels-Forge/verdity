"""
GitLab Platform — webhook verification, MR event normalization, and MR commenting.

GitLab webhook verification uses a shared secret token sent in X-Gitlab-Token header.
Unlike GitHub's HMAC, GitLab compares the token directly (constant-time).
"""

from __future__ import annotations

import hmac
import logging
from typing import Any

import httpx

from verdity.platforms.base import Platform

logger = logging.getLogger(__name__)


class GitLabPlatform(Platform):
    """
    GitLab platform implementation.

    Verification: Shared secret token in X-Gitlab-Token header.
    Event normalization: Converts GitLab MR webhook payloads to Verdity internal format.
    """

    PLATFORM_NAME = "gitlab"

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
        async with httpx.AsyncClient(timeout=30.0) as client:
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
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
