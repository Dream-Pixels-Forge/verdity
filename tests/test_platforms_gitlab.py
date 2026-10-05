"""
Tests for the GitLab platform: verification, event normalization, comment posting.

Phase 13: Multi-Platform Webhook Support — GitLab
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from verdity.platforms.gitlab import GitLabPlatform
from verdity.schemas import TriggerType, VerdityEvent


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


class TestGitLabHandleWebhook:
    """Handle webhook: verify + normalize in one call."""

    @pytest.mark.asyncio
    async def test_handle_webhook_rejects_when_no_secret_configured(self):
        """An unconfigured webhook secret must fail closed with 401."""
        platform = GitLabPlatform()

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {"x-gitlab-token": "anything"}
        mock_request.body = AsyncMock(return_value=b"{}")

        with patch("verdity.platforms.gitlab.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.gitlab_webhook_secret.get_secret_value.return_value = ""
            mock_get_settings.return_value = mock_settings
            with pytest.raises(HTTPException) as exc:
                await platform.handle_webhook(mock_request)

        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_handle_webhook_success(self):
        """Valid webhook returns normalized VerdityEvent."""
        platform = GitLabPlatform()

        # Create a mock Request
        mock_request = MagicMock(spec=Request)
        mock_request.headers = {
            "x-gitlab-token": "secret-token",
            "x-gitlab-event-uuid": "delivery-123",
        }
        mock_request.body = AsyncMock(
            return_value=b'{"object_kind": "merge_request", "object_attributes": {"action": "open", "iid": 42, "title": "Test", "description": "", "head_commit_sha": "abc", "target_commit_sha": "def", "author": {"username": "alice"}}, "project": {"namespace": "myorg", "name": "myrepo"}}'
        )

        with patch.object(platform, "verify_webhook", return_value=True):
            with patch("verdity.platforms.gitlab.get_settings") as mock_get_settings:
                mock_settings = MagicMock()
                mock_settings.gitlab_webhook_secret.get_secret_value.return_value = "secret-token"
                mock_get_settings.return_value = mock_settings
                event = await platform.handle_webhook(mock_request)

        assert isinstance(event, VerdityEvent)
        assert event.trigger_type.value == "pr.opened"
        assert event.repo.owner == "myorg"
        assert event.repo.name == "myrepo"
        assert event.pull_request.number == 42

    @pytest.mark.asyncio
    async def test_handle_webhook_invalid_signature(self):
        """Invalid signature raises HTTPException."""
        platform = GitLabPlatform()

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {"x-gitlab-token": "wrong-token"}
        mock_request.body = AsyncMock(return_value=b"{}")

        with patch.object(platform, "verify_webhook", return_value=False):
            with pytest.raises(Exception):  # HTTPException or similar
                await platform.handle_webhook(mock_request)

    @pytest.mark.asyncio
    async def test_handle_webhook_invalid_signature_raises(self):
        """Invalid signature raises HTTPException with 401."""
        platform = GitLabPlatform()

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {"x-gitlab-token": "wrong-token"}
        mock_request.body = AsyncMock(return_value=b"{}")

        with patch("verdity.platforms.gitlab.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.gitlab_webhook_secret.get_secret_value.return_value = "secret-token"
            mock_get_settings.return_value = mock_settings

            with pytest.raises(HTTPException) as exc_info:
                await platform.handle_webhook(mock_request)

        assert exc_info.value.status_code == 401
        assert "Invalid or missing signature" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_handle_webhook_json_decode_error(self):
        """Invalid JSON raises HTTPException with 400."""
        platform = GitLabPlatform()

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {"x-gitlab-token": "secret-token"}
        mock_request.body = AsyncMock(return_value=b"invalid json")

        with patch("verdity.platforms.gitlab.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.gitlab_webhook_secret.get_secret_value.return_value = "secret-token"
            mock_get_settings.return_value = mock_settings

            with patch.object(platform, "verify_webhook", return_value=True):
                with pytest.raises(HTTPException) as exc_info:
                    await platform.handle_webhook(mock_request)

        assert exc_info.value.status_code == 400
        assert "Invalid JSON payload" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_handle_webhook_unknown_trigger_type(self):
        """Unknown trigger type falls back to PR_OPENED."""
        platform = GitLabPlatform()

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {
            "x-gitlab-token": "secret-token",
            "x-gitlab-event-uuid": "delivery-123",
        }
        # Use an action that maps to unknown trigger type
        mock_request.body = AsyncMock(
            return_value=b'{"object_kind": "merge_request", "object_attributes": {"action": "unknown_action", "iid": 1, "title": "Test", "description": "", "head_commit_sha": "abc", "target_commit_sha": "def", "author": {"username": "alice"}}, "project": {"namespace": "myorg", "name": "myrepo", "id": 123}}'
        )

        with patch("verdity.platforms.gitlab.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.gitlab_webhook_secret.get_secret_value.return_value = "secret-token"
            mock_get_settings.return_value = mock_settings

            with patch.object(platform, "verify_webhook", return_value=True):
                event = await platform.handle_webhook(mock_request)

        assert event.trigger_type == TriggerType.PR_OPENED


class TestGitLabGetMergeRequest:
    """Fetch merge request details from GitLab API."""

    @pytest.mark.asyncio
    async def test_get_merge_request_success(self):
        """Fetch MR details by project_id and MR IID."""
        platform = GitLabPlatform()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": 1,
            "iid": 42,
            "title": "Test MR",
            "description": "Description",
            "state": "opened",
            "head_commit_sha": "abc123",
            "target_commit_sha": "def456",
        }

        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.gitlab.httpx.AsyncClient", return_value=mock_http):
            result = await platform.get_merge_request("myorg/myproj", 42)

        assert result["iid"] == 42
        assert result["title"] == "Test MR"
        call_args = mock_http.get.call_args
        assert "myorg/myproj" in call_args.args[0]
        assert "merge_requests/42" in call_args.args[0]

    @pytest.mark.asyncio
    async def test_get_merge_request_not_found(self):
        """404 raises exception."""
        platform = GitLabPlatform()
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("404 Not Found")

        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.gitlab.httpx.AsyncClient", return_value=mock_http):
            with pytest.raises(Exception):
                await platform.get_merge_request("myorg/myproj", 999)

    @pytest.mark.asyncio
    async def test_get_merge_request_http_error(self):
        """HTTP error raises exception."""
        platform = GitLabPlatform()
        import httpx

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=MagicMock()
        )

        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.gitlab.httpx.AsyncClient", return_value=mock_http):
            with pytest.raises(httpx.HTTPStatusError):
                await platform.get_merge_request("myorg/myproj", 999)


class TestGitLabGetDiff:
    """Fetch MR diff from GitLab API."""

    @pytest.mark.asyncio
    async def test_get_diff_success(self):
        """Fetch diff as string."""
        platform = GitLabPlatform()
        mock_response = MagicMock()
        # GitLab API returns a list of diff objects, each with a "diff" field
        mock_response.json.return_value = [
            {"diff": "diff --git a/file.py b/file.py\n+new line"},
            {"diff": "diff --git a/other.py b/other.py\n-changed line"},
        ]

        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.gitlab.httpx.AsyncClient", return_value=mock_http):
            result = await platform.get_diff("myorg/myproj", 42)

        assert "diff --git" in result
        assert "new line" in result
        assert "changed line" in result
        call_args = mock_http.get.call_args
        assert "merge_requests/42/diffs" in call_args.args[0]

    @pytest.mark.asyncio
    async def test_get_diff_http_error(self):
        """HTTP error raises exception."""
        platform = GitLabPlatform()
        import httpx

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=MagicMock()
        )

        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.gitlab.httpx.AsyncClient", return_value=mock_http):
            with pytest.raises(httpx.HTTPStatusError):
                await platform.get_diff("myorg/myproj", 999)


class TestGitLabGetFileContent:
    """Fetch file content from GitLab repository."""

    @pytest.mark.asyncio
    async def test_get_file_content_success(self):
        """Fetch file content at specific ref."""
        platform = GitLabPlatform()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "content": "ZGVmIGZvbygpOgogICAgcmV0dXJuICJoZWxsbyIK",
            "encoding": "base64",
        }

        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.gitlab.httpx.AsyncClient", return_value=mock_http):
            result = await platform.get_file_content("myorg/myproj", "src/main.py", "abc123")

        assert "def foo():" in result
        call_args = mock_http.get.call_args
        assert "repository/files" in call_args.args[0]
        assert call_args.kwargs["params"]["ref"] == "abc123"

    @pytest.mark.asyncio
    async def test_get_file_content_non_base64(self):
        """Fetch file content with non-base64 encoding."""
        platform = GitLabPlatform()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "content": "plain text content",
            "encoding": "text",
        }

        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.gitlab.httpx.AsyncClient", return_value=mock_http):
            result = await platform.get_file_content("myorg/myproj", "src/main.py", "abc123")

        assert result == "plain text content"

    @pytest.mark.asyncio
    async def test_get_file_content_http_error(self):
        """HTTP error raises exception."""
        platform = GitLabPlatform()
        import httpx

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=MagicMock()
        )

        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.gitlab.httpx.AsyncClient", return_value=mock_http):
            with pytest.raises(httpx.HTTPStatusError):
                await platform.get_file_content("myorg/myproj", "src/main.py", "abc123")
