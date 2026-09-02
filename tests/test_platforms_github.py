"""Tests for GitHub platform: webhook verification, normalization, comment posting."""

from __future__ import annotations

import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verdity.platforms.github import GitHubPlatform


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestVerifyWebhook:
    def test_valid_signature(self):
        platform = GitHubPlatform()
        body = b'{"action":"opened"}'
        secret = "my-secret"
        sig = _sign(secret, body)
        headers = {"x-hub-signature-256": sig}
        assert platform.verify_webhook(headers, body, secret) is True

    def test_invalid_signature(self):
        platform = GitHubPlatform()
        body = b'{"action":"opened"}'
        headers = {"x-hub-signature-256": "sha256=wrong"}
        assert platform.verify_webhook(headers, body, "secret") is False

    def test_missing_signature_header(self):
        platform = GitHubPlatform()
        body = b'{"action":"opened"}'
        headers: dict[str, str] = {}
        assert platform.verify_webhook(headers, body, "secret") is False


class TestNormalizeEvent:
    def test_normalize_pull_request_opened(self):
        platform = GitHubPlatform()
        body = {
            "action": "opened",
            "pull_request": {
                "number": 5,
                "head": {"sha": "head-sha"},
                "base": {"sha": "base-sha"},
                "title": "PR title",
                "body": "PR body",
                "user": {"login": "octocat"},
            },
            "repository": {"name": "repo", "owner": {"login": "owner"}, "id": 1},
        }
        headers = {
            "x-github-event": "pull_request",
            "x-github-delivery": "del-123",
        }
        result = platform.normalize_event(headers, body)
        assert result["trigger_type"] == "pr.opened"
        assert result["delivery_id"] == "del-123"
        assert result["repo"]["owner"] == "owner"
        assert result["repo"]["name"] == "repo"
        assert result["pull_request"]["number"] == 5
        assert result["pull_request"]["head_sha"] == "head-sha"
        assert result["pull_request"]["author"] == "octocat"

    def test_normalize_synchronize(self):
        platform = GitHubPlatform()
        body = {"action": "synchronize", "pull_request": {}, "repository": {}}
        headers = {"x-github-event": "pull_request"}
        result = platform.normalize_event(headers, body)
        assert result["trigger_type"] == "pr.synchronize"

    def test_normalize_reopened(self):
        platform = GitHubPlatform()
        body = {"action": "reopened", "pull_request": {}, "repository": {}}
        headers = {"x-github-event": "pull_request"}
        result = platform.normalize_event(headers, body)
        assert result["trigger_type"] == "pr.reopened"

    def test_normalize_closed(self):
        platform = GitHubPlatform()
        body = {"action": "closed", "pull_request": {}, "repository": {}}
        headers = {"x-github-event": "pull_request"}
        result = platform.normalize_event(headers, body)
        assert result["trigger_type"] == "pr.closed"

    def test_normalize_unknown_action(self):
        platform = GitHubPlatform()
        body = {"action": "labeled", "pull_request": {}, "repository": {}}
        headers = {"x-github-event": "pull_request"}
        result = platform.normalize_event(headers, body)
        assert result["trigger_type"] == "unknown"

    def test_normalize_non_pull_request_event(self):
        platform = GitHubPlatform()
        body = {"repository": {}}
        headers = {"x-github-event": "push"}
        result = platform.normalize_event(headers, body)
        assert result["trigger_type"] == "unknown"
        assert result["action"] == "push"


class TestPostComment:
    @pytest.mark.asyncio
    async def test_post_comment_success(self):
        platform = GitHubPlatform()
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 99, "body": "comment text"}

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.github.httpx.AsyncClient", return_value=mock_http):
            result = await platform.post_comment(
                owner="acme", repo="widgets", number=42, body="hello"
            )
        assert result == {"id": 99, "body": "comment text"}
        mock_http.post.assert_awaited_once()
        call_args = mock_http.post.call_args
        assert "acme/widgets" in call_args.args[0]
        assert call_args.kwargs["json"] == {"body": "hello"}


class TestPostInlineComment:
    @pytest.mark.asyncio
    async def test_post_inline_comment_success(self):
        platform = GitHubPlatform()
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 100}

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.github.httpx.AsyncClient", return_value=mock_http):
            result = await platform.post_inline_comment(
                owner="acme",
                repo="widgets",
                number=42,
                commit_sha="abc123",
                file_path="src/auth.py",
                line=10,
                body="inline comment",
            )
        assert result == {"id": 100}
        call_args = mock_http.post.call_args
        payload = call_args.kwargs["json"]
        assert payload["commit_id"] == "abc123"
        assert payload["comments"][0]["path"] == "src/auth.py"
        assert payload["comments"][0]["position"] == 10


class TestPlatformName:
    def test_platform_name(self):
        assert GitHubPlatform.PLATFORM_NAME == "github"
