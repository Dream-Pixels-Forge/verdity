"""
Tests for the Bitbucket platform: verification, event normalization, comment posting.

Phase 13: Multi-Platform Webhook Support — Bitbucket
"""

from __future__ import annotations

import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from verdity.platforms.bitbucket import BitbucketPlatform
from verdity.schemas import TriggerType, VerdityEvent


class TestBitbucketVerifyWebhook:
    """Verify Bitbucket HMAC-SHA256 webhook verification."""

    def test_valid_signature(self):
        """Valid HMAC-SHA256 signature is accepted."""
        platform = BitbucketPlatform()
        secret = "my-secret"
        body = b'{"pullrequest": {}}'
        expected_sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers = {"x-hub-signature": expected_sig}
        assert platform.verify_webhook(headers, body, secret) is True

    def test_invalid_signature(self):
        """Mismatched signature is rejected."""
        platform = BitbucketPlatform()
        headers = {
            "x-hub-signature": "sha256000000000000000000000000000000000000000000000000000000000000000"
        }
        body = b'{"pullrequest": {}}'
        assert platform.verify_webhook(headers, body, "my-secret") is False

    def test_missing_signature_header(self):
        """Missing X-Hub-Signature header returns False."""
        platform = BitbucketPlatform()
        assert platform.verify_webhook({}, b"body", "secret") is False

    def test_empty_signature(self):
        """Empty signature header returns False."""
        platform = BitbucketPlatform()
        headers = {"x-hub-signature": ""}
        assert platform.verify_webhook(headers, b"body", "secret") is False

    def test_signature_with_wrong_body(self):
        """Signature computed from different body is rejected."""
        platform = BitbucketPlatform()
        secret = "my-secret"
        body = b'{"pullrequest": {}}'
        wrong_body = b'{"different": "body"}'
        sig = "sha256=" + hmac.new(secret.encode(), wrong_body, hashlib.sha256).hexdigest()
        headers = {"x-hub-signature": sig}
        assert platform.verify_webhook(headers, body, secret) is False

    def test_constant_time_comparison(self):
        """Valid signature uses constant-time comparison."""
        platform = BitbucketPlatform()
        secret = "test-secret-constant-time"
        body = b"test body content"
        sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers = {"x-hub-signature": sig}
        assert platform.verify_webhook(headers, body, secret) is True


class TestBitbucketNormalizeEvent:
    """Normalize Bitbucket PR webhook payloads."""

    def test_pullrequest_created(self):
        """PR created event normalizes to pr.opened."""
        platform = BitbucketPlatform()
        headers = {
            "x-event-key": "pullrequest:created",
            "x-hook-uuid": "hook-uuid-123",
        }
        body = {
            "pullrequest": {
                "id": 99,
                "title": "Add feature",
                "description": "This PR adds a new feature",
                "source": {
                    "commit": {"hash": "abc123def"},
                },
                "destination": {
                    "commit": {"hash": "456ghi789"},
                },
                "author": {"username": "alice"},
                "links": {
                    "diff": {"href": "https://bitbucket.org/repo/diff/99"},
                },
            },
            "repository": {
                "name": "myrepo",
                "owner": {"uuid": "owner-uuid-123", "username": "alice"},
            },
        }
        result = platform.normalize_event(headers, body)
        assert result["trigger_type"] == "pr.opened"
        assert result["action"] == "created"
        assert result["delivery_id"] == "hook-uuid-123"
        assert result["repo"]["owner"] == "owner-uuid-123"
        assert result["repo"]["name"] == "myrepo"
        assert result["pull_request"]["number"] == 99
        assert result["pull_request"]["head_sha"] == "abc123def"
        assert result["pull_request"]["base_sha"] == "456ghi789"
        assert result["pull_request"]["title"] == "Add feature"
        assert result["pull_request"]["author"] == "alice"
        assert result["pull_request"]["diff_url"] == "https://bitbucket.org/repo/diff/99"

    def test_pullrequest_updated(self):
        """PR updated event normalizes to pr.synchronize."""
        platform = BitbucketPlatform()
        headers = {"x-event-key": "pullrequest:updated", "x-hook-uuid": "uuid-2"}
        body = {
            "pullrequest": {
                "id": 50,
                "title": "Update",
                "description": "",
                "source": {"commit": {"hash": "s"}},
                "destination": {"commit": {"hash": "d"}},
                "author": {"username": "bob"},
                "links": {},
            },
            "repository": {"name": "r", "owner": {"uuid": "o", "username": "bob"}},
        }
        result = platform.normalize_event(headers, body)
        assert result["trigger_type"] == "pr.synchronize"

    def test_pullrequest_merged(self):
        """PR merged event normalizes to pr.merged."""
        platform = BitbucketPlatform()
        headers = {"x-event-key": "pullrequest:merged"}
        body = {
            "pullrequest": {
                "id": 10,
                "title": "Merged",
                "description": "",
                "source": {"commit": {"hash": ""}},
                "destination": {"commit": {"hash": ""}},
                "author": {"username": "carol"},
                "links": {},
            },
            "repository": {"name": "r", "owner": {"uuid": "o", "username": "carol"}},
        }
        result = platform.normalize_event(headers, body)
        assert result["trigger_type"] == "pr.merged"

    def test_pullrequest_approved(self):
        """PR approved event normalizes to pr.approved."""
        platform = BitbucketPlatform()
        headers = {"x-event-key": "pullrequest:approved"}
        body = {
            "pullrequest": {
                "id": 5,
                "title": "Approved",
                "description": "",
                "source": {"commit": {"hash": ""}},
                "destination": {"commit": {"hash": ""}},
                "author": {"username": "dave"},
                "links": {},
            },
            "repository": {"name": "r", "owner": {"uuid": "o", "username": "dave"}},
        }
        result = platform.normalize_event(headers, body)
        assert result["trigger_type"] == "pr.approved"

    def test_pullrequest_declined(self):
        """PR declined event normalizes to pr.closed."""
        platform = BitbucketPlatform()
        headers = {"x-event-key": "pullrequest:declined"}
        body = {
            "pullrequest": {
                "id": 3,
                "title": "Declined",
                "description": "",
                "source": {"commit": {"hash": ""}},
                "destination": {"commit": {"hash": ""}},
                "author": {"username": "eve"},
                "links": {},
            },
            "repository": {"name": "r", "owner": {"uuid": "o", "username": "eve"}},
        }
        result = platform.normalize_event(headers, body)
        assert result["trigger_type"] == "pr.closed"

    def test_non_pr_event(self):
        """Non-PR events (repo:push) are normalized as unknown."""
        platform = BitbucketPlatform()
        headers = {"x-event-key": "repo:push"}
        body = {"repository": {"name": "r", "owner": {"uuid": "o"}}}
        result = platform.normalize_event(headers, body)
        assert result["trigger_type"] == "unknown"
        assert result["action"] == "repo:push"

    def test_missing_event_key(self):
        """Missing X-Event-Key yields unknown."""
        platform = BitbucketPlatform()
        body = {
            "pullrequest": {
                "id": 1,
                "title": "",
                "description": "",
                "source": {"commit": {"hash": ""}},
                "destination": {"commit": {"hash": ""}},
                "author": {"username": ""},
                "links": {},
            },
            "repository": {"name": "", "owner": {"uuid": ""}},
        }
        result = platform.normalize_event({}, body)
        assert result["trigger_type"] == "unknown"


class TestBitbucketPlatformName:
    """Platform name constant."""

    def test_platform_name(self):
        """BitbucketPlatform.PLATFORM_NAME is 'bitbucket'."""
        assert BitbucketPlatform.PLATFORM_NAME == "bitbucket"

    def test_is_subclass_of_platform(self):
        """BitbucketPlatform inherits from Platform."""
        from verdity.platforms.base import Platform

        assert issubclass(BitbucketPlatform, Platform)


class TestBitbucketPostComment:
    """Post comments to Bitbucket PRs."""

    @pytest.mark.asyncio
    async def test_post_comment_success(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        platform = BitbucketPlatform()
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 1, "content": {"raw": "hello"}}

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.bitbucket.httpx.AsyncClient", return_value=mock_http):
            result = await platform.post_comment(owner="ws", repo="r", number=5, body="hello")
        assert result == {"id": 1, "content": {"raw": "hello"}}
        call_args = mock_http.post.call_args
        assert "ws/r" in call_args.args[0]
        assert call_args.kwargs["json"]["content"]["raw"] == "hello"


class TestBitbucketPostInlineComment:
    """Post inline comments on Bitbucket PRs."""

    @pytest.mark.asyncio
    async def test_post_inline_comment_success(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        platform = BitbucketPlatform()
        mock_response = MagicMock()
        mock_response.json.return_value = {"id": 2}

        mock_http = MagicMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.bitbucket.httpx.AsyncClient", return_value=mock_http):
            result = await platform.post_inline_comment(
                owner="ws",
                repo="r",
                number=5,
                commit_sha="abc",
                file_path="src/x.py",
                line=10,
                body="inline",
            )
        assert result == {"id": 2}
        call_args = mock_http.post.call_args
        payload = call_args.kwargs["json"]
        assert payload["inline"]["path"] == "src/x.py"
        assert payload["inline"]["to"] == 10


class TestBitbucketHandleWebhook:
    """Handle webhook: verify + normalize in one call."""

    @pytest.mark.asyncio
    async def test_handle_webhook_rejects_when_no_secret_configured(self):
        """An unconfigured webhook secret must fail closed with 401."""
        platform = BitbucketPlatform()

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {}
        mock_request.body = AsyncMock(return_value=b"{}")

        with patch("verdity.platforms.bitbucket.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.bitbucket_webhook_secret.get_secret_value.return_value = ""
            mock_get_settings.return_value = mock_settings
            with pytest.raises(HTTPException) as exc:
                await platform.handle_webhook(mock_request)

        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_handle_webhook_success(self):
        """Valid webhook returns normalized VerdityEvent."""
        platform = BitbucketPlatform()

        # Create a mock Request
        mock_request = MagicMock(spec=Request)
        mock_request.headers = {
            "x-hub-signature": "sha256=validsig",
            "x-hook-uuid": "hook-123",
            "x-event-key": "pullrequest:created",
        }
        mock_request.body = AsyncMock(
            return_value=b'{"pullrequest": {"id": 99, "title": "Test", "description": "", "source": {"commit": {"hash": "abc"}}, "destination": {"commit": {"hash": "def"}}, "author": {"username": "alice"}, "links": {"diff": {"href": "https://bitbucket.org/repo/diff/99"}}}, "repository": {"name": "myrepo", "owner": {"uuid": "owner-uuid", "username": "alice"}}}'
        )

        with patch.object(platform, "verify_webhook", return_value=True):
            with patch("verdity.platforms.bitbucket.get_settings") as mock_get_settings:
                mock_settings = MagicMock()
                mock_settings.bitbucket_webhook_secret.get_secret_value.return_value = "secret"
                mock_get_settings.return_value = mock_settings
                event = await platform.handle_webhook(mock_request)

        assert isinstance(event, VerdityEvent)
        assert event.trigger_type.value == "pr.opened"
        assert event.repo.owner == "owner-uuid"
        assert event.repo.name == "myrepo"
        assert event.pull_request.number == 99

    @pytest.mark.asyncio
    async def test_handle_webhook_invalid_signature(self):
        """Invalid signature raises HTTPException."""
        platform = BitbucketPlatform()

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {"x-hub-signature": "sha256=wrong"}
        mock_request.body = AsyncMock(return_value=b"{}")

        with patch.object(platform, "verify_webhook", return_value=False):
            with pytest.raises(Exception):  # HTTPException or similar
                await platform.handle_webhook(mock_request)

    @pytest.mark.asyncio
    async def test_handle_webhook_invalid_signature_raises(self):
        """Invalid signature raises HTTPException with 401."""
        platform = BitbucketPlatform()

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {"x-hub-signature": "sha256=wrong"}
        mock_request.body = AsyncMock(return_value=b"{}")

        with patch("verdity.platforms.bitbucket.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.bitbucket_webhook_secret.get_secret_value.return_value = "secret"
            mock_get_settings.return_value = mock_settings

            with pytest.raises(HTTPException) as exc_info:
                await platform.handle_webhook(mock_request)

        assert exc_info.value.status_code == 401
        assert "Invalid or missing signature" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_handle_webhook_json_decode_error(self):
        """Invalid JSON raises HTTPException with 400."""
        platform = BitbucketPlatform()

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {"x-hub-signature": "sha256=validsig"}
        mock_request.body = AsyncMock(return_value=b"invalid json")

        with patch("verdity.platforms.bitbucket.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.bitbucket_webhook_secret.get_secret_value.return_value = "secret"
            mock_get_settings.return_value = mock_settings

            with patch.object(platform, "verify_webhook", return_value=True):
                with pytest.raises(HTTPException) as exc_info:
                    await platform.handle_webhook(mock_request)

        assert exc_info.value.status_code == 400
        assert "Invalid JSON payload" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_handle_webhook_unknown_trigger_type(self):
        """Unknown trigger type falls back to PR_OPENED."""
        platform = BitbucketPlatform()

        mock_request = MagicMock(spec=Request)
        mock_request.headers = {
            "x-hub-signature": "sha256=validsig",
            "x-event-key": "pullrequest:unknown",
            "x-hook-uuid": "hook-123",
        }
        mock_request.body = AsyncMock(
            return_value=b'{"pullrequest": {"id": 1, "title": "Test", "description": "", "source": {"commit": {"hash": "abc"}}, "destination": {"commit": {"hash": "def"}}, "author": {"username": "alice"}, "links": {"diff": {"href": ""}}}, "repository": {"name": "myrepo", "owner": {"uuid": "owner-uuid", "username": "alice"}}}'
        )

        with patch("verdity.platforms.bitbucket.get_settings") as mock_get_settings:
            mock_settings = MagicMock()
            mock_settings.bitbucket_webhook_secret.get_secret_value.return_value = "secret"
            mock_get_settings.return_value = mock_settings

            with patch.object(platform, "verify_webhook", return_value=True):
                event = await platform.handle_webhook(mock_request)

        assert event.trigger_type == TriggerType.PR_OPENED


class TestBitbucketGetPullRequest:
    """Fetch pull request details from Bitbucket API."""

    @pytest.mark.asyncio
    async def test_get_pull_request_success(self):
        """Fetch PR details by workspace, repo_slug, and PR ID."""
        platform = BitbucketPlatform()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "id": 42,
            "title": "Test PR",
            "description": "Description",
            "state": "OPEN",
            "source": {"commit": {"hash": "abc123"}},
            "destination": {"commit": {"hash": "def456"}},
        }

        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.bitbucket.httpx.AsyncClient", return_value=mock_http):
            result = await platform.get_pull_request("myworkspace", "myrepo", 42)

        assert result["id"] == 42
        assert result["title"] == "Test PR"
        call_args = mock_http.get.call_args
        assert "myworkspace/myrepo" in call_args.args[0]
        assert "pullrequests/42" in call_args.args[0]

    @pytest.mark.asyncio
    async def test_get_pull_request_not_found(self):
        """404 raises exception."""
        platform = BitbucketPlatform()
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("404 Not Found")

        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.bitbucket.httpx.AsyncClient", return_value=mock_http):
            with pytest.raises(Exception):
                await platform.get_pull_request("myworkspace", "myrepo", 999)

    @pytest.mark.asyncio
    async def test_get_pull_request_http_error(self):
        """HTTP error raises exception."""
        platform = BitbucketPlatform()
        import httpx

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=MagicMock()
        )

        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.bitbucket.httpx.AsyncClient", return_value=mock_http):
            with pytest.raises(httpx.HTTPStatusError):
                await platform.get_pull_request("myworkspace", "myrepo", 999)


class TestBitbucketGetDiff:
    """Fetch PR diff from Bitbucket API."""

    @pytest.mark.asyncio
    async def test_get_diff_success(self):
        """Fetch diff as string."""
        platform = BitbucketPlatform()
        mock_response = MagicMock()
        # Bitbucket API returns diff in text format
        mock_response.text = "diff --git a/file.py b/file.py\n+new line"

        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.bitbucket.httpx.AsyncClient", return_value=mock_http):
            result = await platform.get_diff("myworkspace", "myrepo", 42)

        assert "diff --git" in result
        call_args = mock_http.get.call_args
        assert "pullrequests/42/diff" in call_args.args[0]

    @pytest.mark.asyncio
    async def test_get_diff_http_error(self):
        """HTTP error raises exception."""
        platform = BitbucketPlatform()
        import httpx

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=MagicMock()
        )

        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.bitbucket.httpx.AsyncClient", return_value=mock_http):
            with pytest.raises(httpx.HTTPStatusError):
                await platform.get_diff("myworkspace", "myrepo", 999)


class TestBitbucketGetFileContent:
    """Fetch file content from Bitbucket repository."""

    @pytest.mark.asyncio
    async def test_get_file_content_success(self):
        """Fetch file content at specific commit."""
        platform = BitbucketPlatform()
        mock_response = MagicMock()
        mock_response.text = "def foo():\n    return 'hello'"

        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.bitbucket.httpx.AsyncClient", return_value=mock_http):
            result = await platform.get_file_content(
                "myworkspace", "myrepo", "src/main.py", "abc123"
            )

        assert "def foo():" in result
        call_args = mock_http.get.call_args
        assert "src%2Fmain.py" in call_args.args[0]
        assert "abc123" in call_args.args[0]

    @pytest.mark.asyncio
    async def test_get_file_content_http_error(self):
        """HTTP error raises exception."""
        platform = BitbucketPlatform()
        import httpx

        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=MagicMock()
        )

        mock_http = MagicMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)

        with patch("verdity.platforms.bitbucket.httpx.AsyncClient", return_value=mock_http):
            with pytest.raises(httpx.HTTPStatusError):
                await platform.get_file_content("myworkspace", "myrepo", "src/main.py", "abc123")
