"""
Tests for Redis-backed Rate Limiter (Issue #41).

The Redis rate limiter should:
- Be behind a feature flag (REDIS_RATE_LIMITER_ENABLED)
- Replace in-memory token bucket when enabled
- Use redis.asyncio for async operations
- Support sliding window rate limiting
- Fall back gracefully when Redis is unavailable
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from verdity.gateway.app import _RateLimiter


class MockRedis:
    """Mock Redis client for testing."""

    def __init__(self):
        self.eval = AsyncMock(return_value=1)
        self.evalsha = AsyncMock(return_value=[1, 0])
        self.ping = AsyncMock(return_value=True)
        self.close = AsyncMock()
        self.script_load = AsyncMock(return_value="mock_sha")
        self.from_url = MagicMock(return_value=self)


class TestRedisRateLimiter:
    """Tests for Redis-backed rate limiter."""

    @pytest_asyncio.fixture
    async def redis_rate_limiter(self):
        """Create a RedisRateLimiter instance for testing with mocked Redis."""
        # Create mock Redis client
        mock_redis = MockRedis()

        # Patch redis.asyncio.from_url to return our mock
        with patch("redis.asyncio.from_url", return_value=mock_redis):
            from verdity.gateway.app import RedisRateLimiter

            limiter = RedisRateLimiter(
                redis_url="redis://localhost:6379/0",
                max_requests=100,
                window_seconds=60,
            )
            await limiter.connect()
            yield limiter, mock_redis
            await limiter.close()

    @pytest.mark.asyncio
    async def test_redis_rate_limiter_allows_under_limit(self, redis_rate_limiter):
        """Request under limit should be allowed."""
        from starlette.requests import Request

        limiter, mock_redis = redis_rate_limiter

        # Create a mock request
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/verdity/webhooks/github",
            "headers": [],
            "client": ("192.168.1.1", 12345),
        }
        request = Request(scope)

        allowed, retry_after = await limiter.is_allowed(request)
        assert allowed is True
        assert retry_after == 0.0

    @pytest.mark.asyncio
    async def test_redis_rate_limiter_rejects_over_limit(self, redis_rate_limiter):
        """Request over limit should be rejected with retry-after."""
        from starlette.requests import Request

        limiter, mock_redis = redis_rate_limiter
        # Mock Redis to return 0 (rate limited)
        mock_redis.evalsha = AsyncMock(return_value=[0, 30])

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/verdity/webhooks/github",
            "headers": [],
            "client": ("192.168.1.1", 12345),
        }
        request = Request(scope)

        allowed, retry_after = await limiter.is_allowed(request)
        assert allowed is False
        assert retry_after > 0

    @pytest.mark.asyncio
    async def test_redis_rate_limiter_fallback_on_redis_error(self, redis_rate_limiter):
        """Should fall back to in-memory limiter when Redis fails."""
        from starlette.requests import Request

        limiter, mock_redis = redis_rate_limiter
        # Mock Redis to raise an exception
        mock_redis.evalsha = AsyncMock(side_effect=Exception("Redis down"))

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/verdity/webhooks/github",
            "headers": [],
            "client": ("192.168.1.1", 12345),
        }
        request = Request(scope)

        # Should fall back to in-memory limiter
        allowed, retry_after = await limiter.is_allowed(request)
        assert allowed is True  # First request should be allowed

    @pytest.mark.asyncio
    async def test_redis_rate_limiter_uses_sliding_window(self, redis_rate_limiter):
        """Should use sliding window algorithm via Lua script."""
        from starlette.requests import Request

        limiter, mock_redis = redis_rate_limiter

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/verdity/webhooks/github",
            "headers": [],
            "client": ("192.168.1.1", 12345),
        }
        request = Request(scope)

        await limiter.is_allowed(request)

        # Verify Lua script was called
        mock_redis.evalsha.assert_called()

    @pytest.mark.asyncio
    async def test_redis_rate_limiter_different_ips_independent(self, redis_rate_limiter):
        """Different IPs should have independent limits."""
        from starlette.requests import Request

        limiter, mock_redis = redis_rate_limiter

        scope1 = {
            "type": "http",
            "method": "POST",
            "path": "/verdity/webhooks/github",
            "headers": [],
            "client": ("192.168.1.1", 12345),
        }
        scope2 = {
            "type": "http",
            "method": "POST",
            "path": "/verdity/webhooks/github",
            "headers": [],
            "client": ("192.168.1.2", 12345),
        }
        request1 = Request(scope1)
        request2 = Request(scope2)

        # Both should be allowed (different IPs)
        allowed1, _ = await limiter.is_allowed(request1)
        allowed2, _ = await limiter.is_allowed(request2)

        assert allowed1 is True
        assert allowed2 is True

    @pytest.mark.asyncio
    async def test_redis_rate_limiter_close(self, redis_rate_limiter):
        """Close should properly close Redis connection."""
        limiter, mock_redis = redis_rate_limiter
        await limiter.close()
        mock_redis.close.assert_called_once()

    def test_redis_rate_limiter_not_available_without_feature_flag(self):
        """RedisRateLimiter should not be used when feature flag is off."""
        from verdity.config import get_settings
        from verdity.gateway import app

        # The app should use _RateLimiter (in-memory) when Redis is disabled
        # This is tested via the gateway_client fixture which sets _RateLimiter


class TestRedisRateLimiterConfig:
    """Tests for Redis rate limiter configuration."""

    def test_redis_rate_limiter_disabled_by_default(self):
        """Redis rate limiter should be disabled by default."""
        from verdity.config import get_settings

        settings = get_settings()
        assert settings.redis_rate_limiter_enabled is False

    def test_redis_rate_limiter_enabled_via_env(self):
        """Redis rate limiter can be enabled via environment variable."""
        import os
        from verdity.config import get_settings

        os.environ["REDIS_RATE_LIMITER_ENABLED"] = "true"
        get_settings.cache_clear()
        try:
            settings = get_settings()
            assert settings.redis_rate_limiter_enabled is True
        finally:
            del os.environ["REDIS_RATE_LIMITER_ENABLED"]
            get_settings.cache_clear()

    def test_redis_url_config(self):
        """Redis URL should be configurable."""
        import os
        from verdity.config import get_settings

        os.environ["REDIS_URL"] = "redis://custom:6379/1"
        get_settings.cache_clear()
        try:
            settings = get_settings()
            assert settings.redis_url == "redis://custom:6379/1"
        finally:
            del os.environ["REDIS_URL"]
            get_settings.cache_clear()
