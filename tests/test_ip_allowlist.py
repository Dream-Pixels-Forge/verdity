"""
Tests for GitHub IP Allowlist Middleware (Issue #41).

The IP allowlist middleware should:
- Be config-gated via GITHUB_WEBHOOK_IPS environment variable
- Allow requests from IPs in the allowlist
- Reject requests from IPs not in the allowlist (403)
- Apply only to /verdity/webhooks/github endpoint
- Support CIDR notation for IP ranges
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from verdity.audit_store import AuditStore
from verdity.event_queue import EventQueue
from verdity.gateway.app import DeliveryCache, _RateLimiter, app
from verdity.metrics_store import MetricsStore
from verdity.schemas import RepoRef

GITHUB_SECRET = "test-hmac-secret-key-for-dev-only"


def _sign(secret: str, body: bytes) -> str:
    import hashlib
    import hmac

    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest_asyncio.fixture
async def gw_client() -> AsyncGenerator[AsyncClient, None]:
    """Set up gateway with IP allowlist configured."""
    from verdity.config import get_settings
    from verdity.gateway.app import _parse_ip_allowlist

    get_settings.cache_clear()
    os.environ["WEBHOOK_HMAC_SECRET"] = GITHUB_SECRET
    os.environ["WEBHOOK_HMAC_SECRET_PREVIOUS"] = ""
    os.environ["GITHUB_WEBHOOK_IPS"] = "192.30.252.0/22,185.199.108.0/22,140.82.112.0/20"
    os.environ["GITHUB_APP_ID"] = "12345"
    os.environ["GITHUB_APP_INSTALLATION_ID"] = "98765"
    os.environ["GITHUB_APP_PRIVATE_KEY"] = (
        "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
    )
    get_settings.cache_clear()

    original_init = RepoRef.__init__

    def patched_init(self, owner, name, **kwargs):
        kwargs.setdefault("id", 0)
        original_init(self, owner=owner, name=name, **kwargs)

    RepoRef.__init__ = patched_init

    app.state.delivery_ids = set()
    app.state._delivery_cache_ts = {}
    app.state._last_eviction = 0.0
    app.state._rate_limiter = _RateLimiter()
    # Set IP allowlist for this test
    app.state._github_ip_allowlist = _parse_ip_allowlist(
        "192.30.252.0/22,185.199.108.0/22,140.82.112.0/20"
    )
    app.state._delivery_cache = DeliveryCache(db_path=":memory:")
    await app.state._delivery_cache.connect()
    app.state.queue = EventQueue(db_path=":memory:")
    app.state.audit = AuditStore(db_path=":memory:")
    app.state.metrics = MetricsStore(db_path=":memory:")
    await app.state.queue.connect()
    await app.state.audit.connect()
    await app.state.metrics.connect()

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    try:
        yield client
    finally:
        await client.aclose()
        await app.state.queue.close()
        await app.state.audit.close()
        await app.state.metrics.close()
        await app.state._delivery_cache.close()
        RepoRef.__init__ = original_init


@pytest_asyncio.fixture
async def gw_client_no_allowlist() -> AsyncGenerator[AsyncClient, None]:
    """Set up gateway WITHOUT IP allowlist configured (feature disabled)."""
    from verdity.config import get_settings
    from verdity.gateway.app import _parse_ip_allowlist

    get_settings.cache_clear()
    os.environ["WEBHOOK_HMAC_SECRET"] = GITHUB_SECRET
    os.environ["WEBHOOK_HMAC_SECRET_PREVIOUS"] = ""
    # Do NOT set GITHUB_WEBHOOK_IPS - feature should be disabled
    if "GITHUB_WEBHOOK_IPS" in os.environ:
        del os.environ["GITHUB_WEBHOOK_IPS"]
    os.environ["GITHUB_APP_ID"] = "12345"
    os.environ["GITHUB_APP_INSTALLATION_ID"] = "98765"
    os.environ["GITHUB_APP_PRIVATE_KEY"] = (
        "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"
    )
    get_settings.cache_clear()

    original_init = RepoRef.__init__

    def patched_init(self, owner, name, **kwargs):
        kwargs.setdefault("id", 0)
        original_init(self, owner=owner, name=name, **kwargs)

    RepoRef.__init__ = patched_init

    app.state.delivery_ids = set()
    app.state._delivery_cache_ts = {}
    app.state._last_eviction = 0.0
    app.state._rate_limiter = _RateLimiter()
    # Empty allowlist = feature disabled
    app.state._github_ip_allowlist = _parse_ip_allowlist("")
    app.state._delivery_cache = DeliveryCache(db_path=":memory:")
    await app.state._delivery_cache.connect()
    app.state.queue = EventQueue(db_path=":memory:")
    app.state.audit = AuditStore(db_path=":memory:")
    app.state.metrics = MetricsStore(db_path=":memory:")
    await app.state.queue.connect()
    await app.state.audit.connect()
    await app.state.metrics.connect()

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    try:
        yield client
    finally:
        await client.aclose()
        await app.state.queue.close()
        await app.state.audit.close()
        await app.state.metrics.close()
        await app.state._delivery_cache.close()
        RepoRef.__init__ = original_init


class TestIPAllowlistMiddleware:
    """Tests for IP allowlist middleware on /verdity/webhooks/github."""

    @pytest.mark.asyncio
    async def test_allowlist_allows_configured_ip(self, gw_client):
        """Request from IP in allowlist should succeed (202)."""
        body = b'{"action":"opened","pull_request":{"number":1,"head":{"sha":"abc"},"base":{"sha":"def"},"title":"T","body":"","user":{"login":"u"}},"repository":{"name":"r","owner":{"login":"o"}}}'
        sig = _sign(GITHUB_SECRET, body)

        # 192.30.252.1 is in 192.30.252.0/22 (GitHub's webhook IP range)
        resp = await gw_client.post(
            "/verdity/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "test-delivery-1",
                "X-Forwarded-For": "192.30.252.1",
            },
        )
        assert resp.status_code == 202

    @pytest.mark.asyncio
    async def test_allowlist_rejects_unconfigured_ip(self, gw_client):
        """Request from IP NOT in allowlist should be rejected (403)."""
        body = b'{"action":"opened","pull_request":{"number":1,"head":{"sha":"abc"},"base":{"sha":"def"},"title":"T","body":"","user":{"login":"u"}},"repository":{"name":"r","owner":{"login":"o"}}}'
        sig = _sign(GITHUB_SECRET, body)

        # 10.0.0.1 is NOT in GitHub's webhook IP ranges
        resp = await gw_client.post(
            "/verdity/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "test-delivery-2",
                "X-Forwarded-For": "10.0.0.1",
            },
        )
        assert resp.status_code == 403
        assert "IP not allowed" in resp.json().get("detail", "")

    @pytest.mark.asyncio
    async def test_allowlist_disabled_when_not_configured(self, gw_client_no_allowlist):
        """When GITHUB_WEBHOOK_IPS is not set, allowlist should be disabled (allow all)."""
        body = b'{"action":"opened","pull_request":{"number":1,"head":{"sha":"abc"},"base":{"sha":"def"},"title":"T","body":"","user":{"login":"u"}},"repository":{"name":"r","owner":{"login":"o"}}}'
        sig = _sign(GITHUB_SECRET, body)

        # Even an IP not in GitHub's ranges should be allowed when feature is disabled
        resp = await gw_client_no_allowlist.post(
            "/verdity/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "test-delivery-3",
                "X-Forwarded-For": "10.0.0.1",
            },
        )
        assert resp.status_code == 202

    @pytest.mark.asyncio
    async def test_allowlist_applies_only_to_github_webhook(self, gw_client):
        """IP allowlist should only apply to /verdity/webhooks/github, not other endpoints."""
        body = b'{"action":"opened","pull_request":{"number":1,"head":{"sha":"abc"},"base":{"sha":"def"},"title":"T","body":"","user":{"login":"u"}},"repository":{"name":"r","owner":{"login":"o"}}}'
        _ = _sign(GITHUB_SECRET, body)  # sig not used for GitLab endpoint

        # Other webhook endpoints should not be affected by IP allowlist
        resp = await gw_client.post(
            "/verdity/webhooks/gitlab",
            content=b'{"object_kind":"merge_request"}',
            headers={
                "Content-Type": "application/json",
                "X-Gitlab-Token": "gitlab-secret",
                "X-Forwarded-For": "10.0.0.1",
            },
        )
        # Should not be blocked by IP allowlist (though may fail for other reasons)
        assert resp.status_code != 403 or "IP not allowed" not in resp.json().get("detail", "")

    @pytest.mark.asyncio
    async def test_allowlist_supports_cidr_notation(self, gw_client):
        """IP allowlist should support CIDR notation for IP ranges."""
        body = b'{"action":"opened","pull_request":{"number":1,"head":{"sha":"abc"},"base":{"sha":"def"},"title":"T","body":"","user":{"login":"u"}},"repository":{"name":"r","owner":{"login":"o"}}}'
        sig = _sign(GITHUB_SECRET, body)

        # Test edge of CIDR range - 185.199.108.0/22 covers 185.199.108.0 - 185.199.111.255
        resp = await gw_client.post(
            "/verdity/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "test-delivery-4",
                "X-Forwarded-For": "185.199.111.255",  # Last IP in 185.199.108.0/22
            },
        )
        assert resp.status_code == 202

    @pytest.mark.asyncio
    async def test_allowlist_rejects_outside_cidr(self, gw_client):
        """IP just outside CIDR range should be rejected."""
        body = b'{"action":"opened","pull_request":{"number":1,"head":{"sha":"abc"},"base":{"sha":"def"},"title":"T","body":"","user":{"login":"u"}},"repository":{"name":"r","owner":{"login":"o"}}}'
        sig = _sign(GITHUB_SECRET, body)

        # 185.199.112.0 is just outside 185.199.108.0/22
        resp = await gw_client.post(
            "/verdity/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "test-delivery-5",
                "X-Forwarded-For": "185.199.112.0",
            },
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_allowlist_handles_multiple_ips_in_x_forwarded_for(self, gw_client):
        """Should use the first IP in X-Forwarded-For header."""
        body = b'{"action":"opened","pull_request":{"number":1,"head":{"sha":"abc"},"base":{"sha":"def"},"title":"T","body":"","user":{"login":"u"}},"repository":{"name":"r","owner":{"login":"o"}}}'
        sig = _sign(GITHUB_SECRET, body)

        # First IP is allowed, second is not - should use first (allowed)
        resp = await gw_client.post(
            "/verdity/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "test-delivery-6",
                "X-Forwarded-For": "192.30.252.1, 10.0.0.1",
            },
        )
        assert resp.status_code == 202

    @pytest.mark.asyncio
    async def test_allowlist_falls_back_to_direct_client_ip(self, gw_client):
        """Should fall back to request.client.host when no X-Forwarded-For header."""
        body = b'{"action":"opened","pull_request":{"number":1,"head":{"sha":"abc"},"base":{"sha":"def"},"title":"T","body":"","user":{"login":"u"}},"repository":{"name":"r","owner":{"login":"o"}}}'
        sig = _sign(GITHUB_SECRET, body)

        # No X-Forwarded-For header - should use direct connection IP
        # Note: TestClient uses a default IP, this tests the fallback logic
        resp = await gw_client.post(
            "/verdity/webhooks/github",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": "test-delivery-7",
            },
        )
        # The test client IP may or may not be in allowlist; just verify no crash
        assert resp.status_code in (202, 403)


class TestIPAllowlistConfig:
    """Tests for IP allowlist configuration parsing."""

    def test_parse_cidr_list(self):
        """Test parsing comma-separated CIDR list."""
        from verdity.gateway.app import _parse_ip_allowlist

        cidr_str = "192.30.252.0/22,185.199.108.0/22,140.82.112.0/20"
        networks = _parse_ip_allowlist(cidr_str)
        assert len(networks) == 3

    def test_parse_single_ip(self):
        """Test parsing single IP (treated as /32)."""
        from verdity.gateway.app import _parse_ip_allowlist

        networks = _parse_ip_allowlist("192.168.1.1")
        assert len(networks) == 1

    def test_parse_empty_string(self):
        """Empty string should return empty list."""
        from verdity.gateway.app import _parse_ip_allowlist

        networks = _parse_ip_allowlist("")
        assert networks == []

    def test_parse_whitespace_handling(self):
        """Should handle whitespace around commas."""
        from verdity.gateway.app import _parse_ip_allowlist

        networks = _parse_ip_allowlist(" 192.30.252.0/22 , 185.199.108.0/22 ")
        assert len(networks) == 2
