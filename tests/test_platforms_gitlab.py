"""
Tests for the GitLab platform: verification, event normalization, comment posting.

Phase 13: Multi-Platform Webhook Support — GitLab
"""

from __future__ import annotations

import pytest

from verdity.platforms.gitlab import GitLabPlatform


class TestGitLabVerifyWebhook:
    """Verify GitLab webhook token comparison."""

    def test_valid_token(self):
        """Valid token matches the shared secret."""
        platform = GitLabPlatform()
        headers = {"x-gitlab-token": "my-secret-token"}
        body = b'{"object_kind": "merge_request"}'
        assert platform.verify_webhook(headers, body, "my-secret-token") is True

    def test_invalid_token(self):
        """Mismatched token is rejected."""
        platform = GitLabPlatform()
        headers = {"x-gitlab-token": "wrong-token"}
        body = b'{"object_kind": "merge_request"}'
        assert platform.verify_webhook(headers, body, "my-secret-token") is False

    def test_missing_token_header(self):
        """Missing X-Gitlab-Token header returns False."""
        platform = GitLabPlatform()
        headers: dict[str, str] = {}
        body = b'{"object_kind": "merge_request"}'
        assert platform.verify_webhook(headers, body, "secret") is False

    def test_empty_secret(self):
        """Empty configured secret returns False."""
        platform = GitLabPlatform()
        headers = {"x-gitlab-token": "any-token"}
        body = b"{}"
        assert platform.verify_webhook(headers, body, "") is False

    def test_empty_token_with_secret(self):
        """Empty token in header with a secret returns False."""
        platform = GitLabPlatform()
        headers = {"x-gitlab-token": ""}
        body = b"{}"
        assert platform.verify_webhook(headers, body, "secret") is False

    def test_both_empty(self):
        """Both empty — returns False."""
        platform = GitLabPlatform()
        assert platform.verify_webhook({}, b"", "") is False

    def test_case_sensitive(self):
        """Token comparison is case-sensitive."""
        platform = GitLabPlatform()
        headers = {"x-gitlab-token": "Secret-Token"}
        body = b"{}"
        assert platform.verify_webhook(headers, body, "secret-token") is False
        assert platform.verify_webhook(headers, body, "Secret-Token") is True


class TestGitLabNormalizeEvent:
    """Normalize GitLab merge request webhook payloads."""

    def test_merge_request_opened(self):
        """MR opened event normalizes to pr.opened."""
        platform = GitLabPlatform()
        headers = {"x-gitlab-event-uuid": "delivery-123"}
        body = {
            "object_kind": "merge_request",
            "object_attributes": {
                "action": "open",
                "iid": 42,
                "title": "Fix auth bug",
                "description": "Fixes the auth bypass",
                "head_commit_sha": "abc123",
                "target_commit_sha": "def456",
                "author": {"username": "alice"},
            },
            "project": {
                "namespace": "myorg",
                "name": "myrepo",
            },
        }
        result = platform.normalize_event(headers, body)
        assert result["trigger_type"] == "pr.opened"
        assert result["action"] == "open"
        assert result["delivery_id"] == "delivery-123"
        assert result["repo"]["owner"] == "myorg"
        assert result["repo"]["name"] == "myrepo"
        assert result["pull_request"]["number"] == 42
        assert result["pull_request"]["head_sha"] == "abc123"
        assert result["pull_request"]["title"] == "Fix auth bug"
        assert result["pull_request"]["author"] == "alice"

    def test_merge_request_updated(self):
        """MR updated event normalizes to pr.synchronize."""
        platform = GitLabPlatform()
        headers = {"x-gitlab-event-uuid": "delivery-456"}
        body = {
            "object_kind": "merge_request",
            "object_attributes": {
                "action": "update",
                "iid": 10,
                "title": "Update",
                "description": "",
                "head_commit_sha": "aaa",
                "target_commit_sha": "bbb",
                "author": {"username": "bob"},
            },
            "project": {
                "namespace": "team",
                "name": "proj",
            },
        }
        result = platform.normalize_event(headers, body)
        assert result["trigger_type"] == "pr.synchronize"

    def test_merge_request_merged(self):
        """MR merged event normalizes to pr_merged."""
        platform = GitLabPlatform()
        headers = {}
        body = {
            "object_kind": "merge_request",
            "object_attributes": {
                "action": "merge",
                "iid": 5,
                "title": "Merged",
                "description": "",
                "head_commit_sha": "x",
                "target_commit_sha": "y",
                "author": {"username": "carol"},
            },
            "project": {"namespace": "t", "name": "p"},
        }
        result = platform.normalize_event(headers, body)
        assert result["trigger_type"] == "pr.merged"

    def test_merge_request_closed(self):
        """MR closed event normalizes to pr.closed."""
        platform = GitLabPlatform()
        body = {
            "object_kind": "merge_request",
            "object_attributes": {
                "action": "close",
                "iid": 1,
                "title": "Closed",
                "description": "",
                "head_commit_sha": "",
                "target_commit_sha": "",
                "author": {"username": "dave"},
            },
            "project": {"namespace": "", "name": ""},
        }
        result = platform.normalize_event({}, body)
        assert result["trigger_type"] == "pr.closed"

    def test_merge_request_reopen(self):
        """MR reopen event normalizes to pr.reopened."""
        platform = GitLabPlatform()
        body = {
            "object_kind": "merge_request",
            "object_attributes": {
                "action": "reopen",
                "iid": 7,
                "title": "Reopened",
                "description": "",
                "head_commit_sha": "",
                "target_commit_sha": "",
                "author": {"username": "eve"},
            },
            "project": {"namespace": "", "name": ""},
        }
        result = platform.normalize_event({}, body)
        assert result["trigger_type"] == "pr.reopened"

    def test_non_merge_request_event(self):
        """Push events are normalized as unknown."""
        platform = GitLabPlatform()
        body = {"object_kind": "push"}
        result = platform.normalize_event({}, body)
        assert result["trigger_type"] == "unknown"
        assert result["action"] == "push"

    def test_diff_refs_missing(self):
        """Missing diff_refs does not crash."""
        platform = GitLabPlatform()
        body = {
            "object_kind": "merge_request",
            "object_attributes": {
                "action": "open",
                "iid": 1,
                "title": "T",
                "description": "",
                "head_commit_sha": "",
                "target_commit_sha": "",
                "author": {"username": "x"},
            },
            "project": {"namespace": "", "name": ""},
        }
        result = platform.normalize_event({}, body)
        assert result["pull_request"]["diff_url"] == ""


class TestGitLabPlatformName:
    """Platform name constant."""

    def test_platform_name(self):
        """GitLabPlatform.PLATFORM_NAME is 'gitlab'."""
        assert GitLabPlatform.PLATFORM_NAME == "gitlab"

    def test_is_subclass_of_platform(self):
        """GitLabPlatform inherits from Platform."""
        from verdity.platforms.base import Platform

        assert issubclass(GitLabPlatform, Platform)

    def test_repr_includes_class_and_platform(self):
        """Platform.__repr__ returns '<ClassName platform=name>'."""
        platform = GitLabPlatform()
        assert repr(platform) == "<GitLabPlatform platform=gitlab>"


class TestGitLabPostComment:
    """Post comments to GitLab MRs."""

    @pytest.mark.asyncio
    async def test_post_comment_success(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        platform = GitLabPlatform()
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 1, "body": "ok"}

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.gitlab.httpx.AsyncClient", return_value=mock_http):
            result = await platform.post_comment(owner="ns", repo="proj", number=10, body="hello")
        assert result == {"id": 1, "body": "ok"}
        call_args = mock_http.post.call_args
        assert "ns/proj" in call_args.args[0]
        assert call_args.kwargs["json"] == {"body": "hello"}


class TestGitLabPostInlineComment:
    """Post inline discussions on GitLab MRs."""

    @pytest.mark.asyncio
    async def test_post_inline_comment_success(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        platform = GitLabPlatform()
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 2}

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.gitlab.httpx.AsyncClient", return_value=mock_http):
            result = await platform.post_inline_comment(
                owner="ns",
                repo="proj",
                number=10,
                commit_sha="abc",
                file_path="src/x.py",
                line=5,
                body="inline",
            )
        assert result == {"id": 2}
        call_args = mock_http.post.call_args
        payload = call_args.kwargs["json"]
        assert payload["body"] == "inline"
        assert payload["position"]["new_path"] == "src/x.py"
        assert payload["position"]["new_line"] == 5
