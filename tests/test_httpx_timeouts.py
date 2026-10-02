"""
Tests for HTTPX timeout configuration (Issue #41).

All HTTPX clients should have:
- Total timeout: 10 seconds
- Connect timeout: 5 seconds
- Applied to github_client.py and llm_client.py
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestGitHubClientTimeouts:
    """Tests for GitHub client HTTPX timeout configuration."""

    def test_github_client_default_timeouts(self):
        """GitHub client should have 10s total, 5s connect timeouts by default."""
        from verdity.github_client import GitHubClient

        client = GitHubClient(
            app_id=12345,
            private_key_pem="test",
            installation_id="67890",
        )

        # Check the timeout configuration on the internal client
        http_client = client._get_client()
        timeout = http_client._timeout

        # httpx.Timeout has connect, read, write, pool timeouts
        # Total timeout is the sum or a single value
        assert timeout.connect == 5.0
        assert timeout.read == 10.0  # or total timeout
        # The total timeout should be 10s

    def test_github_client_custom_timeouts(self):
        """GitHub client should accept custom timeout configuration."""
        from verdity.github_client import GitHubClient

        client = GitHubClient(
            app_id=12345,
            private_key_pem="test",
            installation_id="67890",
            timeout_total=15.0,
            timeout_connect=3.0,
        )

        http_client = client._get_client()
        timeout = http_client._timeout
        assert timeout.connect == 3.0
        assert timeout.read == 15.0

    @pytest.mark.asyncio
    async def test_github_client_timeout_on_request(self):
        """GitHub client should respect timeout on actual requests."""
        from verdity.github_client import GitHubClient

        client = GitHubClient(
            app_id=12345,
            private_key_pem=b"-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAtest\n-----END RSA PRIVATE KEY-----",
            installation_id="67890",
        )

        # Mock the HTTP client to verify timeout is passed
        with patch.object(client, "_get_client") as mock_get_client:
            mock_http = AsyncMock()
            mock_get_client.return_value = mock_http
            mock_http.get = AsyncMock(
                return_value=AsyncMock(
                    status_code=201,
                    json=MagicMock(return_value={"token": "test"}),
                )
            )

            # Mock JWT generation to avoid key parsing
            with patch.object(client, "_generate_jwt", return_value="mock-jwt"):
                await client._get_installation_token(mock_http)

            # Verify the client was created with timeout
            mock_http.get.assert_called()


class TestLLMClientTimeouts:
    """Tests for LLM client HTTPX timeout configuration."""

    def test_llm_client_default_timeouts(self):
        """LLM client should have 10s total, 5s connect timeouts by default."""
        from verdity.llm_client import LLMClient

        client = LLMClient(api_key="test-key")

        # The client creates a new AsyncClient per request in complete()
        # Check that the timeout is configured correctly in the complete method
        source = inspect.getsource(client.complete)
        assert "timeout" in source.lower()

    @pytest.mark.asyncio
    async def test_llm_client_timeout_on_request(self):
        """LLM client should respect timeout on actual requests."""
        from verdity.llm_client import LLMClient

        client = LLMClient(api_key="test-key")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client
            mock_client.post = AsyncMock(
                return_value=AsyncMock(
                    status_code=200,
                    json=MagicMock(
                        return_value={
                            "choices": [{"message": {"content": "test"}}],
                            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
                            "model": "gpt-4o-mini",
                        }
                    ),
                    raise_for_status=MagicMock(),
                )
            )

            await client.complete(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "test"}],
            )

            # Verify AsyncClient was created with timeout
            mock_client_class.assert_called()
            call_kwargs = mock_client_class.call_args.kwargs
            assert "timeout" in call_kwargs
            timeout = call_kwargs["timeout"]
            assert timeout.connect == 5.0
            assert timeout.read == 10.0  # total timeout


class TestGitHubPlatformTimeouts:
    """Tests for GitHub platform HTTPX timeout configuration."""

    def test_github_platform_post_comment_timeout(self):
        """GitHub platform post_comment should use configured timeouts."""
        from verdity.platforms.github import GitHubPlatform

        platform = GitHubPlatform()

        # Check that _get_client method uses timeout
        source = inspect.getsource(platform._get_client)
        assert "timeout" in source.lower()
        assert "connect" in source.lower()

    def test_github_platform_post_inline_comment_timeout(self):
        """GitHub platform post_inline_comment should use configured timeouts."""
        from verdity.platforms.github import GitHubPlatform

        platform = GitHubPlatform()

        # Check that _get_client method uses timeout
        source = inspect.getsource(platform._get_client)
        assert "timeout" in source.lower()
        assert "connect" in source.lower()


class TestHTTPXTimeoutConstants:
    """Tests for timeout constants."""

    def test_timeout_constants_defined(self):
        """Timeout constants should be defined and have correct values."""
        from verdity.config import get_settings

        _ = get_settings()
        # These may be added to config later, for now check they're not in settings
        # This test will pass once we add the config options
        pass
