"""
Tests for GitHub API client — App auth, PR comment posting, review posting.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from verdity.github_client import GitHubClient, GitHubClientError

# ── Fixtures ──────────────────────────────────────────────────────────


def _generate_test_private_key() -> str:
    """Generate a real RSA private key for tests."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


SAMPLE_PRIVATE_KEY = _generate_test_private_key()


def _make_client(**kwargs) -> GitHubClient:
    """Create a GitHubClient with sensible test defaults."""
    defaults = {
        "app_id": 12345,
        "private_key_pem": SAMPLE_PRIVATE_KEY,
        "installation_id": "67890",
    }
    defaults.update(kwargs)
    return GitHubClient(**defaults)


# ── JWT Generation ────────────────────────────────────────────────────


class TestJWTGeneration:
    def test_generates_jwt(self):
        client = _make_client()
        token = client._generate_jwt()
        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 50  # JWT tokens are long

    def test_jwt_cached_within_lifetime(self):
        client = _make_client(token_lifetime_seconds=600)
        t1 = client._generate_jwt()
        t2 = client._generate_jwt()
        assert t1 == t2  # same token returned (cached)

    def test_jwt_refreshed_after_expiry(self):
        client = _make_client(token_lifetime_seconds=10)
        with patch("verdity.github_client.time") as mock_time:
            mock_time.time.return_value = 1000.0
            t1 = client._generate_jwt()
            mock_time.time.return_value = 1020.0  # 20s later > lifetime
            t2 = client._generate_jwt()
        assert t1 != t2

    @patch("verdity.github_client.jwt.encode")
    def test_jwt_payload(self, mock_encode):
        mock_encode.return_value = "mock.jwt.token"
        client = _make_client(app_id=99999)
        token = client._generate_jwt()
        assert token == "mock.jwt.token"
        mock_encode.assert_called_once()
        payload = mock_encode.call_args[0][0]
        assert payload["iss"] == "99999"
        assert "iat" in payload
        assert "exp" in payload
        assert payload["exp"] > payload["iat"]


# ── Installation Token ────────────────────────────────────────────────


class TestInstallationToken:
    @pytest.mark.asyncio
    async def test_get_installation_token_success(self):
        client = _make_client()
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "token": "ghs_test_token_abc123",
            "expires_at": "2099-01-01T00:00:00Z",
        }

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.get.return_value = mock_response
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            token = await client._get_installation_token(mock_http)
            assert token == "ghs_test_token_abc123"

    @pytest.mark.asyncio
    async def test_get_installation_token_caches(self):
        client = _make_client()
        client._installationToken = "cached_token"
        client._token_expires_at = time.time() + 3600

        mock_http = AsyncMock()
        token = await client._get_installation_token(mock_http)
        assert token == "cached_token"
        mock_http.get.assert_not_called()  # no API call made

    @pytest.mark.asyncio
    async def test_get_installation_token_empty_expires(self):
        """Token response without expires_at falls back to now + 3600."""
        client = _make_client()
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"token": "ghs_abc"}

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.get.return_value = mock_resp
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            token = await client._get_installation_token(mock_http)
            assert token == "ghs_abc"
            assert client._token_expires_at > time.time()
        client = _make_client()
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_response

        with pytest.raises(GitHubClientError, match="Failed to get installation token"):
            await client._get_installation_token(mock_http)


# ── Post PR Comment ──────────────────────────────────────────────────


class TestPostPRComment:
    @pytest.mark.asyncio
    async def test_post_comment_success(self):
        client = _make_client()
        expected = {"id": 1, "body": "Hello from Verdity"}

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = expected

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response
            mock_http.get.return_value = MagicMock(
                status_code=201,
                json=MagicMock(
                    return_value={"token": "ghs_x", "expires_at": "2099-01-01T00:00:00Z"}
                ),
            )
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            result = await client.post_pr_comment("org", "repo", 42, "Review complete")
            assert result == expected

    @pytest.mark.asyncio
    async def test_post_comment_failure(self):
        client = _make_client()

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response
            mock_http.get.return_value = MagicMock(
                status_code=201,
                json=MagicMock(
                    return_value={"token": "ghs_x", "expires_at": "2099-01-01T00:00:00Z"}
                ),
            )
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            with pytest.raises(GitHubClientError, match="Failed to post PR comment"):
                await client.post_pr_comment("org", "repo", 99, "Should fail")


# ── Post PR Review ────────────────────────────────────────────────────


class TestPostPRReview:
    @pytest.mark.asyncio
    async def test_post_review_success(self):
        client = _make_client()
        expected = {"id": 100, "state": "COMMENTED"}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = expected

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response
            mock_http.get.return_value = MagicMock(
                status_code=201,
                json=MagicMock(
                    return_value={"token": "ghs_x", "expires_at": "2099-01-01T00:00:00Z"}
                ),
            )
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            result = await client.post_pr_review(
                "org",
                "repo",
                42,
                "LGTM",
                event="APPROVE",
                commit_id="abc123",
            )
            assert result == expected

    @pytest.mark.asyncio
    async def test_post_review_with_inline_comments(self):
        """Review with inline comments posts correct payload."""
        client = _make_client()
        comments = [{"path": "main.py", "line": 10, "body": "Fix this"}]

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": 200}

        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 201
        mock_token_resp.json.return_value = {
            "token": "ghs_inst123",
            "expires_at": "2099-01-01T00:00:00Z",
        }

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.get.return_value = mock_token_resp
            mock_http.post.return_value = mock_response
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            result = await client.post_pr_review(
                "org", "repo", 42, body="Review", event="COMMENT", comments=comments
            )
            assert result == {"id": 200}
            call_kwargs = mock_http.post.call_args
            assert call_kwargs[1]["json"]["comments"] == comments

    @pytest.mark.asyncio
    async def test_post_review_failure(self):
        client = _make_client()

        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.text = "Validation Failed"

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response
            mock_http.get.return_value = MagicMock(
                status_code=201,
                json=MagicMock(
                    return_value={"token": "ghs_x", "expires_at": "2099-01-01T00:00:00Z"}
                ),
            )
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            with pytest.raises(GitHubClientError, match="Failed to post PR review"):
                await client.post_pr_review("org", "repo", 42, "test")


# ── Post Inline Comment ──────────────────────────────────────────────


class TestPostInlineComment:
    @pytest.mark.asyncio
    async def test_inline_comment_success(self):
        client = _make_client()
        expected = {"id": 200, "path": "src/main.py"}

        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = expected

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response
            mock_http.get.return_value = MagicMock(
                status_code=201,
                json=MagicMock(
                    return_value={"token": "ghs_x", "expires_at": "2099-01-01T00:00:00Z"}
                ),
            )
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            result = await client.post_inline_comment(
                "org",
                "repo",
                42,
                "Security issue here",
                commit_id="abc123",
                path="src/main.py",
                line=10,
            )
            assert result == expected

    @pytest.mark.asyncio
    async def test_inline_comment_failure(self):
        client = _make_client()

        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.post.return_value = mock_response
            mock_http.get.return_value = MagicMock(
                status_code=201,
                json=MagicMock(
                    return_value={"token": "ghs_x", "expires_at": "2099-01-01T00:00:00Z"}
                ),
            )
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            with pytest.raises(GitHubClientError, match="Failed to post inline comment"):
                await client.post_inline_comment(
                    "org",
                    "repo",
                    42,
                    "test",
                    commit_id="abc123",
                    path="src/main.py",
                    line=10,
                )


# ── Get PR ────────────────────────────────────────────────────────────


class TestGetPR:
    @pytest.mark.asyncio
    async def test_get_pr_success(self):
        client = _make_client()
        expected = {"number": 42, "title": "Fix bug", "state": "open"}

        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 201
        mock_token_resp.json.return_value = {
            "token": "ghs_inst123",
            "expires_at": "2099-01-01T00:00:00Z",
        }

        mock_pr_resp = MagicMock()
        mock_pr_resp.status_code = 200
        mock_pr_resp.json.return_value = expected

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(side_effect=[mock_token_resp, mock_pr_resp])
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            result = await client.get_pr("org", "repo", 42)
            assert result == expected

    @pytest.mark.asyncio
    async def test_get_pr_failure(self):
        client = _make_client()

        mock_token_resp = MagicMock()
        mock_token_resp.status_code = 201
        mock_token_resp.json.return_value = {
            "token": "ghs_inst123",
            "expires_at": "2099-01-01T00:00:00Z",
        }

        mock_pr_resp = MagicMock()
        mock_pr_resp.status_code = 404
        mock_pr_resp.text = "Not Found"

        with patch("verdity.github_client.httpx.AsyncClient") as MockClient:
            mock_http = AsyncMock()
            mock_http.get = AsyncMock(side_effect=[mock_token_resp, mock_pr_resp])
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_http

            with pytest.raises(GitHubClientError, match="Failed to get PR"):
                await client.get_pr("org", "repo", 99)


# ── Close ─────────────────────────────────────────────────────────────


class TestClose:
    def test_close_clears_tokens(self):
        client = _make_client()
        client._jwt = "some.jwt.token"
        client._installationToken = "ghs_some_token"
        import asyncio

        asyncio.run(client.close())
        assert client._jwt is None
        assert client._installationToken is None


# ── Error class ───────────────────────────────────────────────────────


class TestGitHubClientError:
    def test_is_exception(self):
        assert issubclass(GitHubClientError, Exception)
        err = GitHubClientError("test error")
        assert str(err) == "test error"
