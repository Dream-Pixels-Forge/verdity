"""
Tests for the Bitbucket platform: verification, event normalization, comment posting.

Phase 13: Multi-Platform Webhook Support — Bitbucket
"""

from __future__ import annotations

import hashlib
import hmac

import pytest

from verdity.platforms.bitbucket import BitbucketPlatform


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
