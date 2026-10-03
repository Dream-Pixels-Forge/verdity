"""
GitHub API client for posting PR review comments.

Handles GitHub App authentication (JWT → installation token),
and provides typed methods for posting PR comments and reviews.

This is the output path: findings flow from the orchestrator through
the router to this client, which posts them as GitHub PR comments.

HTTPX timeout configuration (Issue #41):
  - Total timeout: 10 seconds (configurable via VERDITY_HTTP_TIMEOUT_TOTAL)
  - Connect timeout: 5 seconds (configurable via VERDITY_HTTP_TIMEOUT_CONNECT)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Self

import httpx
import jwt  # PyJWT

from verdity.config import get_settings
from verdity.schemas import Finding, Severity

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


# ── GitHub Checks API Helpers ───────────────────────────────────────────


def create_annotations(findings: list[Finding]) -> list[dict[str, Any]]:
    """
    Convert findings to GitHub Checks API annotations format.

    GitHub limits annotations to 50 per check run.
    """
    annotations: list[dict[str, Any]] = []

    for finding in findings:
        # Determine annotation level based on severity
        if finding.severity in (Severity.CRITICAL, Severity.HIGH):
            annotation_level = "failure"
        else:
            annotation_level = "warning"

        # Main annotation for the finding
        annotations.append(
            {
                "path": finding.file,
                "start_line": finding.line_start,
                "end_line": finding.line_end,
                "annotation_level": annotation_level,
                "message": finding.summary,
                "title": finding.concern.value,
            }
        )

        # Add auto-fix suggestion as notice annotation if available
        if finding.suggested_fix_diff:
            annotations.append(
                {
                    "path": finding.file,
                    "start_line": finding.line_start,
                    "end_line": finding.line_end,
                    "annotation_level": "notice",
                    "message": f"Suggested fix: {finding.suggested_fix_diff}",
                    "title": "Auto-fix available",
                }
            )

    # GitHub API limit: max 50 annotations
    return annotations[:50]


def create_check_output(findings: list[Finding]) -> dict[str, Any]:
    """
    Create rich check run output with markdown summary and annotations.

    Includes title, summary, detailed text, and annotations (limited to 50).
    """
    if not findings:
        return {
            "title": "Verdity Code Review",
            "summary": "## Summary\n\nFound **0 issues** in this PR.",
            "text": "## Summary\n\nNo issues found. Great job! 🎉",
            "annotations": [],
        }

    # Group findings by concern type
    from collections import defaultdict

    by_concern: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        by_concern[finding.concern.value].append(finding)

    # Build markdown text
    text_parts = [
        "## Summary",
        "",
        f"Found **{len(findings)} issues** in this PR.",
        "",
    ]

    # Add breakdown by concern
    if by_concern:
        text_parts.append("### By Category")
        text_parts.append("")
        for concern, concern_findings in sorted(by_concern.items()):
            text_parts.append(f"- **{concern}**: {len(concern_findings)} issue(s)")
        text_parts.append("")

    # Add details for each finding
    text_parts.append("### Details")
    text_parts.append("")

    for i, finding in enumerate(findings, 1):
        severity_emoji = {
            "critical": "🔴",
            "high": "🟠",
            "medium": "🟡",
            "low": "🔵",
            "info": "⚪",
        }.get(finding.severity.value, "⚪")

        text_parts.append(
            f"#### {i}. {severity_emoji} {finding.concern.value.title()}: {finding.summary}"
        )
        text_parts.append(f"**File:** `{finding.file}` (lines {finding.line_start}-{finding.line_end})")
        text_parts.append(f"**Severity:** {finding.severity.value.upper()}")
        text_parts.append(f"**Confidence:** {finding.confidence:.0%}")
        text_parts.append(f"**Explanation:** {finding.explanation}")
        if finding.suggested_fix_diff:
            text_parts.append(f"**Suggested Fix:**\n```diff\n{finding.suggested_fix_diff}\n```")
        text_parts.append("")

    text = "\n".join(text_parts)

    # Create annotations (limited to 50)
    annotations = create_annotations(findings)

    return {
        "title": "Verdity Code Review",
        "summary": f"## Summary\n\nFound **{len(findings)} issues** in this PR.",
        "text": text,
        "annotations": annotations,
    }


# Default HTTPX timeout configuration (Issue #41)
def _get_default_timeout_total() -> float:
    return get_settings().http_timeout_total


def _get_default_timeout_connect() -> float:
    return get_settings().http_timeout_connect


class GitHubClientError(Exception):
    """Raised when a GitHub API call fails."""


class GitHubClient:
    """
    Authenticated GitHub API client for a GitHub App.

    Usage:
        client = GitHubClient(
            app_id=12345,
            private_key_pem=b"-----BEGIN RSA PRIVATE KEY-----\\n...",
            installation_id="67890",
        )
        await client.post_pr_comment(owner="org", repo="repo", pr_number=42, body="Review complete")
    """

    def __init__(
        self,
        app_id: int,
        private_key_pem: str | bytes,
        installation_id: str,
        *,
        base_url: str = GITHUB_API_BASE,
        token_lifetime_seconds: int = 600,
        timeout_total: float | None = None,
        timeout_connect: float | None = None,
    ) -> None:
        self._app_id = app_id
        self._private_key = (
            private_key_pem if isinstance(private_key_pem, bytes) else private_key_pem.encode()
        )
        self._installation_id = installation_id
        self._base_url = base_url.rstrip("/")
        self._token_lifetime = token_lifetime_seconds
        self._timeout_total = timeout_total or _get_default_timeout_total()
        self._timeout_connect = timeout_connect or _get_default_timeout_connect()

        # Cached tokens
        self._jwt: str | None = None
        self._jwt_issued_at: float = 0.0
        self._installationToken: str | None = None
        self._token_expires_at: float = 0.0

        # Shared HTTP client with connection pooling
        self._client: httpx.AsyncClient | None = None

    # ── Client Management ─────────────────────────────────────────────

    def _get_client(self) -> httpx.AsyncClient:
        """Return the shared HTTP client, creating it lazily if needed."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=self._timeout_connect,
                    read=self._timeout_total,
                    write=self._timeout_total,
                    pool=self._timeout_total,
                ),
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                    keepalive_expiry=30,
                ),
            )
        return self._client

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """
        Make an authenticated HTTP request to the GitHub API.

        Args:
            method: HTTP method (GET, POST, PATCH, etc.)
            url: Full URL to request
            **kwargs: Additional arguments passed to httpx.AsyncClient.request

        Returns:
            httpx.Response object
        """
        client = self._get_client()
        headers = await self._auth_headers(client)
        resp = await client.request(method, url, headers=headers, **kwargs)
        return resp

    # ── Authentication ────────────────────────────────────────────────

    def _generate_jwt(self) -> str:
        """
        Generate a short-lived JWT for GitHub App authentication.
        Per GitHub docs: JWT valid up to 10 minutes; we cache and reuse.
        """
        now = time.time()
        if self._jwt and (now - self._jwt_issued_at) < (self._token_lifetime - 30):
            return self._jwt

        now_int = int(now)
        payload = {
            "iat": now_int,
            "exp": now_int + self._token_lifetime,
            "iss": str(self._app_id),
        }
        self._jwt = jwt.encode(payload, self._private_key, algorithm="RS256")
        self._jwt_issued_at = now
        logger.debug("Generated new GitHub App JWT (app_id=%d)", self._app_id)
        return self._jwt

    async def _get_installation_token(self, client: httpx.AsyncClient) -> str:
        """
        Exchange JWT for an installation access token.
        Tokens are valid for 1 hour; we cache and refresh early.
        """
        now = time.time()
        if self._installationToken and now < (self._token_expires_at - 60):
            return self._installationToken

        jwt_token = self._generate_jwt()
        resp = await client.get(
            f"{self._base_url}/app/installations/{self._installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {jwt_token}",
                "Accept": "application/vnd.github+json",
            },
        )
        if resp.status_code != 201:
            raise GitHubClientError(
                f"Failed to get installation token: {resp.status_code} {resp.text}"
            )

        data = resp.json()
        self._installationToken = data["token"]
        # GitHub returns expires_at as ISO string; parse it
        expires_at = data.get("expires_at", "")
        if expires_at:
            from datetime import datetime

            dt = datetime.fromisoformat(expires_at)
            self._token_expires_at = dt.timestamp()
        else:
            self._token_expires_at = now + 3600

        logger.debug("Obtained installation token (installation_id=%s)", self._installation_id)
        return self._installationToken

    async def _auth_headers(self, client: httpx.AsyncClient) -> dict[str, str]:
        """Return headers with a valid installation token."""
        token = await self._get_installation_token(client)
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    # ── PR Comments ───────────────────────────────────────────────────

    async def post_pr_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
    ) -> dict[str, Any]:
        """
        Post a top-level comment on a PR.
        Returns the GitHub issue comment object.
        """
        client = self._get_client()
        headers = await self._auth_headers(client)
        resp = await client.post(
            f"{self._base_url}/repos/{owner}/{repo}/issues/{pr_number}/comments",
            json={"body": body},
            headers=headers,
        )
        if resp.status_code not in (200, 201):
            raise GitHubClientError(f"Failed to post PR comment: {resp.status_code} {resp.text}")
        logger.info("Posted PR comment on %s/%s#%d", owner, repo, pr_number)
        return resp.json()

    async def post_pr_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
        *,
        event: str = "COMMENT",
        commit_id: str | None = None,
        comments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Post a PR review with optional inline comments.

        event: COMMENT, APPROVE, REQUEST_CHANGES, or DISMISS
        commit_id: SHA of the commit to review (required for inline comments)
        comments: list of inline comment objects [{path, position, body}]
        """
        payload: dict[str, Any] = {"body": body, "event": event}
        if commit_id:
            payload["commit_id"] = commit_id
        if comments:
            payload["comments"] = comments

        client = self._get_client()
        headers = await self._auth_headers(client)
        resp = await client.post(
            f"{self._base_url}/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            json=payload,
            headers=headers,
        )
        if resp.status_code not in (200, 201):
            raise GitHubClientError(f"Failed to post PR review: {resp.status_code} {resp.text}")
        logger.info("Posted PR review on %s/%s#%d (event=%s)", owner, repo, pr_number, event)
        return resp.json()

    async def post_inline_comment(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        body: str,
        *,
        commit_id: str,
        path: str,
        line: int,
        side: str = "RIGHT",
    ) -> dict[str, Any]:
        """
        Post a single inline review comment on a PR diff.
        """
        payload = {
            "body": body,
            "commit_id": commit_id,
            "path": path,
            "line": line,
            "side": side,
        }
        client = self._get_client()
        headers = await self._auth_headers(client)
        resp = await client.post(
            f"{self._base_url}/repos/{owner}/{repo}/pulls/{pull_number}/comments",
            json=payload,
            headers=headers,
        )
        if resp.status_code not in (200, 201):
            raise GitHubClientError(
                f"Failed to post inline comment: {resp.status_code} {resp.text}"
            )
        logger.info(
            "Posted inline comment on %s/%s#%d (%s:%d)",
            owner,
            repo,
            pull_number,
            path,
            line,
        )
        return resp.json()

    # ── GitHub Checks API ───────────────────────────────────────────────

    async def create_check_run(
        self,
        owner: str,
        repo: str,
        name: str,
        head_sha: str,
        status: str = "in_progress",
        conclusion: str | None = None,
        output: dict[str, Any] | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
        actions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Create a new check run on a commit.

        Args:
            owner: Repository owner
            repo: Repository name
            name: Name of the check (e.g., "verdity-review")
            head_sha: SHA of the commit to check
            status: "queued", "in_progress", or "completed"
            conclusion: Required if status is "completed" - "success", "failure", "neutral", etc.
            output: Optional output object with title, summary, text, annotations, images
            started_at: ISO 8601 timestamp when check started
            completed_at: ISO 8601 timestamp when check completed (required if status=completed)
            actions: Optional list of action buttons for the check run

        Returns:
            GitHub check run object
        """
        client = self._get_client()
        headers = await self._auth_headers(client)

        payload: dict[str, Any] = {
            "name": name,
            "head_sha": head_sha,
            "status": status,
        }
        if conclusion:
            payload["conclusion"] = conclusion
        if output:
            payload["output"] = output
        if started_at:
            payload["started_at"] = started_at
        if completed_at:
            payload["completed_at"] = completed_at
        if actions:
            payload["actions"] = actions

        resp = await client.post(
            f"{self._base_url}/repos/{owner}/{repo}/check-runs",
            json=payload,
            headers=headers,
        )
        if resp.status_code not in (200, 201):
            raise GitHubClientError(f"Failed to create check run: {resp.status_code} {resp.text}")
        logger.info("Created check run '%s' on %s/%s@%s", name, owner, repo, head_sha[:7])
        return resp.json()

    async def update_check_run(
        self,
        owner: str,
        repo: str,
        check_run_id: int,
        status: str | None = None,
        conclusion: str | None = None,
        output: dict[str, Any] | None = None,
        completed_at: str | None = None,
        actions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Update an existing check run.

        Args:
            owner: Repository owner
            repo: Repository name
            check_run_id: ID of the check run to update
            status: New status ("queued", "in_progress", "completed")
            conclusion: New conclusion if status is "completed"
            output: Updated output object
            completed_at: ISO 8601 timestamp when check completed
            actions: Optional list of action buttons for the check run

        Returns:
            Updated GitHub check run object
        """
        client = self._get_client()
        headers = await self._auth_headers(client)

        payload: dict[str, Any] = {}
        if status:
            payload["status"] = status
        if conclusion:
            payload["conclusion"] = conclusion
        if output:
            payload["output"] = output
        if completed_at:
            payload["completed_at"] = completed_at
        if actions:
            payload["actions"] = actions

        resp = await client.patch(
            f"{self._base_url}/repos/{owner}/{repo}/check-runs/{check_run_id}",
            json=payload,
            headers=headers,
        )
        if resp.status_code not in (200, 201):
            raise GitHubClientError(f"Failed to update check run: {resp.status_code} {resp.text}")
        logger.info("Updated check run %d on %s/%s", check_run_id, owner, repo)
        return resp.json()

    # ── Utility ───────────────────────────────────────────────────────

    async def get_pr(self, owner: str, repo: str, pr_number: int) -> dict[str, Any]:
        """Fetch PR metadata from GitHub."""
        client = self._get_client()
        headers = await self._auth_headers(client)
        resp = await client.get(
            f"{self._base_url}/repos/{owner}/{repo}/pulls/{pr_number}",
            headers=headers,
        )
        if resp.status_code != 200:
            raise GitHubClientError(f"Failed to get PR: {resp.status_code} {resp.text}")
        return resp.json()

    async def close(self) -> None:
        """Release cached tokens and close the HTTP client."""
        self._jwt = None
        self._installationToken = None
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> Self:
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Async context manager exit - ensures client is closed."""
        await self.close()


    # ── PR Diff Fetching ──────────────────────────────────────────────

    async def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> dict[str, Any] | None:
        """
        Fetch PR diff and file changes from GitHub.

        Returns a dict with:
        - base_sha: base commit SHA
        - head_sha: head commit SHA
        - files: list of file changes with filename, patch, additions, deletions
        """
        client = self._get_client()
        headers = await self._auth_headers(client)

        # Get PR metadata
        pr_resp = await client.get(
            f"{self._base_url}/repos/{owner}/{repo}/pulls/{pr_number}",
            headers=headers,
        )
        if pr_resp.status_code != 200:
            logger.error("Failed to get PR %s/%s#%d: %s", owner, repo, pr_number, pr_resp.text)
            return None

        pr_data = pr_resp.json()
        base_sha = pr_data.get("base", {}).get("sha", "")
        head_sha = pr_data.get("head", {}).get("sha", "")

        # Get PR files (diff)
        files_resp = await client.get(
            f"{self._base_url}/repos/{owner}/{repo}/pulls/{pr_number}/files",
            headers=headers,
        )
        if files_resp.status_code != 200:
            logger.error("Failed to get PR files: %s", files_resp.text)
            return None

        files_data = files_resp.json()

        return {
            "base_sha": base_sha,
            "head_sha": head_sha,
            "files": [
                {
                    "filename": f.get("filename", ""),
                    "patch": f.get("patch", ""),
                    "additions": f.get("additions", 0),
                    "deletions": f.get("deletions", 0),
                    "status": f.get("status", ""),
                }
                for f in files_data
            ],
        }
