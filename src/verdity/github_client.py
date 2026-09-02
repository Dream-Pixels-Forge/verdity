"""
GitHub API client for posting PR review comments.

Handles GitHub App authentication (JWT → installation token),
and provides typed methods for posting PR comments and reviews.

This is the output path: findings flow from the orchestrator through
the router to this client, which posts them as GitHub PR comments.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Self

import httpx
import jwt  # PyJWT

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


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
    ) -> None:
        self._app_id = app_id
        self._private_key = (
            private_key_pem if isinstance(private_key_pem, bytes) else private_key_pem.encode()
        )
        self._installation_id = installation_id
        self._base_url = base_url.rstrip("/")
        self._token_lifetime = token_lifetime_seconds

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
                timeout=30.0,
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                    keepalive_expiry=30,
                ),
            )
        return self._client

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
