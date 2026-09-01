"""
Tests for the Ingestion Gateway endpoint.

Verifies:
  - 202 on valid signature → event queued
  - 401 on invalid signature → NOT queued
  - 409 on replay (duplicate delivery ID)
  - 429 on rate limit exceeded
  - Sub-1s response time (basic latency check)
"""

from __future__ import annotations

import json
import time
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from verdity.event_queue import EventQueue
from verdity.hmac_verify import compute_signature

WEBHOOK_SECRET = b"test-hmac-secret-key-for-dev-only"

# Minimal realistic GitHub pull_request.opened payload
SAMPLE_PAYLOAD = {
    "action": "opened",
    "number": 482,
    "pull_request": {
        "number": 482,
        "head": {"sha": "a1b2c3d4e5f6"},
        "base": {"sha": "f6e5d4c3b2a1"},
        "draft": False,
        "title": "Fix auth bug",
    },
    "repository": {
        "id": 123456,
        "name": "widgets",
        "full_name": "acme/widgets",
        "owner": {"login": "acme"},
    },
    "sender": {"login": "contributor1"},
}


def _make_signable_payload(payload: dict) -> bytes:
    """GitHub signs the raw JSON body — we must send raw bytes, not a parsed dict."""
    return json.dumps(payload, separators=(",", ":")).encode()


def _sign(body: bytes) -> str:
    return compute_signature(WEBHOOK_SECRET, body)


@pytest_asyncio.fixture
async def gateway_client_and_queue() -> AsyncGenerator[tuple[AsyncClient, EventQueue], None]:
    """Create gateway client and expose the queue for inspection."""
    from verdity.audit_store import AuditStore
    from verdity.gateway.app import DeliveryCache, _RateLimiter, app

    app.state.delivery_ids = set()
    app.state._delivery_cache_ts = {}
    app.state._last_eviction = 0.0
    app.state._rate_limiter = _RateLimiter()
    app.state._delivery_cache = DeliveryCache(db_path=":memory:")
    await app.state._delivery_cache.connect()
    app.state.queue = EventQueue(db_path=":memory:")
    app.state.audit = AuditStore(db_path=":memory:")
    await app.state.queue.connect()
    await app.state.audit.connect()

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    try:
        yield client, app.state.queue
    finally:
        await client.aclose()
        await app.state.queue.close()
        await app.state.audit.close()
        await app.state._delivery_cache.close()


@pytest.mark.asyncio
async def test_valid_webhook_returns_202_and_queues(gateway_client_and_queue):
    client, queue = gateway_client_and_queue
    body = _make_signable_payload(SAMPLE_PAYLOAD)
    sig = _sign(body)
    delivery_id = str(uuid.uuid4())

    start = time.monotonic()
    resp = await client.post(
        "/verdity/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sig,
            "X-GitHub-Delivery": delivery_id,
            "Content-Type": "application/json",
        },
    )
    elapsed_ms = (time.monotonic() - start) * 1000

    assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert data["status"] == "queued"
    assert data["delivery_id"] == delivery_id
    assert "message_id" in data
    # Gate requirement: < 1 second
    assert elapsed_ms < 1000, f"Webhook processing took {elapsed_ms:.1f}ms — exceeds 1s gate"

    # Verify event is on the queue
    envelope = await queue.consume()
    assert envelope is not None
    assert envelope.event.trigger_type.value == "pr.opened"
    assert envelope.event.repo.owner == "acme"
    assert envelope.event.repo.name == "widgets"
    assert envelope.event.pull_request is not None
    assert envelope.event.pull_request.number == 482


@pytest.mark.asyncio
async def test_invalid_signature_returns_401(gateway_client_and_queue):
    client, queue = gateway_client_and_queue
    body = _make_signable_payload(SAMPLE_PAYLOAD)
    delivery_id = str(uuid.uuid4())

    resp = await client.post(
        "/verdity/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=invalidsignature00000000000000000000000000000000000000000000000000000000",
            "X-GitHub-Delivery": delivery_id,
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401
    # Nothing should have been queued
    counts = await queue.count_by_state()
    assert counts["pending"] == 0


@pytest.mark.asyncio
async def test_missing_signature_returns_401(gateway_client_and_queue):
    client, _ = gateway_client_and_queue
    body = _make_signable_payload(SAMPLE_PAYLOAD)
    delivery_id = str(uuid.uuid4())

    resp = await client.post(
        "/verdity/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": delivery_id,
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_replay_delivery_returns_409(gateway_client_and_queue):
    client, _ = gateway_client_and_queue
    body = _make_signable_payload(SAMPLE_PAYLOAD)
    sig = _sign(body)
    delivery_id = str(uuid.uuid4())

    resp1 = await client.post(
        "/verdity/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sig,
            "X-GitHub-Delivery": delivery_id,
            "Content-Type": "application/json",
        },
    )
    assert resp1.status_code == 202

    resp2 = await client.post(
        "/verdity/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sig,
            "X-GitHub-Delivery": delivery_id,
            "Content-Type": "application/json",
        },
    )
    assert resp2.status_code == 409


@pytest.mark.asyncio
async def test_health_endpoint(gateway_client_and_queue):
    client, _ = gateway_client_and_queue
    resp = await client.get("/verdity/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_rate_limit_returns_429_after_exceeding_limit(gateway_client_and_queue):
    """Send 101 requests from the same IP — the 101th must be 429."""
    client, _ = gateway_client_and_queue
    body = _make_signable_payload(SAMPLE_PAYLOAD)
    sig = _sign(body)

    # First 100 should succeed (202 or 409 — all within limit)
    for i in range(100):
        delivery_id = str(uuid.uuid4())
        resp = await client.post(
            "/verdity/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Delivery": delivery_id,
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code in (202, 409), (
            f"Request {i + 1} got {resp.status_code}: {resp.text}"
        )

    # The 101st request should be rate-limited
    delivery_id_101 = str(uuid.uuid4())
    resp = await client.post(
        "/verdity/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sig,
            "X-GitHub-Delivery": delivery_id_101,
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 429, f"Expected 429, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "detail" in data
    assert "Retry-After" in resp.headers


@pytest.mark.asyncio
async def test_rate_limit_different_ips_are_independent():
    """Rate limit is per-IP — two different client IPs are tracked separately."""
    from verdity.audit_store import AuditStore
    from verdity.gateway.app import DeliveryCache, _RateLimiter, app

    app.state.delivery_ids = set()
    app.state._delivery_cache_ts = {}
    app.state._last_eviction = 0.0
    app.state._rate_limiter = _RateLimiter()
    app.state._delivery_cache = DeliveryCache(db_path=":memory:")
    await app.state._delivery_cache.connect()
    app.state.queue = EventQueue(db_path=":memory:")
    app.state.audit = AuditStore(db_path=":memory:")
    await app.state.queue.connect()
    await app.state.audit.connect()

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")

    body = _make_signable_payload(SAMPLE_PAYLOAD)
    sig = _sign(body)

    try:
        # Fill up IP A's quota (using X-Forwarded-For to identify IPs)
        for _ in range(100):
            delivery_id = str(uuid.uuid4())
            await client.post(
                "/verdity/webhooks/github",
                content=body,
                headers={
                    "X-GitHub-Event": "pull_request",
                    "X-Hub-Signature-256": sig,
                    "X-GitHub-Delivery": delivery_id,
                    "Content-Type": "application/json",
                    "X-Forwarded-For": "10.0.0.1",
                },
            )

        # IP B should still be able to send (different bucket)
        delivery_id_b = str(uuid.uuid4())
        resp_b = await client.post(
            "/verdity/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Delivery": delivery_id_b,
                "Content-Type": "application/json",
                "X-Forwarded-For": "10.0.0.2",
            },
        )
        assert resp_b.status_code in (202, 409), (
            f"IP B should not be rate-limited by IP A, got {resp_b.status_code}"
        )

        # IP A should now be rate-limited
        delivery_id_a = str(uuid.uuid4())
        resp_a = await client.post(
            "/verdity/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Delivery": delivery_id_a,
                "Content-Type": "application/json",
                "X-Forwarded-For": "10.0.0.1",
            },
        )
        assert resp_a.status_code == 429
    finally:
        await client.aclose()
        await app.state.queue.close()
        await app.state.audit.close()
        await app.state._delivery_cache.close()


@pytest.mark.asyncio
async def test_delivery_cache_persists_across_restart(tmp_path):
    """Delivery IDs persisted to SQLite survive a simulated restart."""
    from verdity.audit_store import AuditStore
    from verdity.gateway.app import DeliveryCache, _RateLimiter, app

    cache_db = str(tmp_path / "delivery_cache.db")
    delivery_id = str(uuid.uuid4())

    # ── First "session": accept a webhook ──────────────────────────────
    cache = DeliveryCache(db_path=cache_db)
    await cache.connect()

    app.state.delivery_ids = set()
    app.state._delivery_cache_ts = {}
    app.state._last_eviction = 0.0
    app.state._rate_limiter = _RateLimiter()
    app.state.queue = EventQueue(db_path=":memory:")
    app.state.audit = AuditStore(db_path=":memory:")
    app.state._delivery_cache = cache
    await app.state.queue.connect()
    await app.state.audit.connect()

    # Seed the delivery ID into the cache (simulates a prior accepted webhook)
    await cache.add(delivery_id)
    app.state.delivery_ids.add(delivery_id)
    app.state._delivery_cache_ts[delivery_id] = time.time()

    await cache.close()
    await app.state.queue.close()
    await app.state.audit.close()

    # ── Second "session": load from DB, verify dedup still works ───────
    cache2 = DeliveryCache(db_path=cache_db)
    await cache2.connect()

    app.state.delivery_ids = set()
    app.state._delivery_cache_ts = {}
    app.state._last_eviction = 0.0
    app.state._rate_limiter = _RateLimiter()
    app.state.queue = EventQueue(db_path=":memory:")
    app.state.audit = AuditStore(db_path=":memory:")
    app.state._delivery_cache = cache2
    await app.state.queue.connect()
    await app.state.audit.connect()

    # Load persisted IDs into the in-memory set
    loaded_ids = await cache2.load_recent()
    app.state.delivery_ids = loaded_ids

    # The previously-seen delivery ID must be detected as a replay
    assert delivery_id in app.state.delivery_ids

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    body = _make_signable_payload(SAMPLE_PAYLOAD)
    sig = _sign(body)
    try:
        resp = await client.post(
            "/verdity/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Delivery": delivery_id,
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 409, f"Expected 409 replay, got {resp.status_code}: {resp.text}"
    finally:
        await client.aclose()
        await cache2.close()
        await app.state.queue.close()
        await app.state.audit.close()


@pytest.mark.asyncio
async def test_delivery_cache_persist_called_on_accept(tmp_path):
    """When a new webhook is accepted, its delivery ID is persisted to the DB."""
    from verdity.audit_store import AuditStore
    from verdity.gateway.app import DeliveryCache, _RateLimiter, app

    cache_db = str(tmp_path / "delivery_cache.db")
    delivery_id = str(uuid.uuid4())

    cache = DeliveryCache(db_path=cache_db)
    await cache.connect()

    app.state.delivery_ids = set()
    app.state._delivery_cache_ts = {}
    app.state._last_eviction = 0.0
    app.state._rate_limiter = _RateLimiter()
    app.state.queue = EventQueue(db_path=":memory:")
    app.state.audit = AuditStore(db_path=":memory:")
    app.state._delivery_cache = cache
    await app.state.queue.connect()
    await app.state.audit.connect()

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    body = _make_signable_payload(SAMPLE_PAYLOAD)
    sig = _sign(body)
    try:
        resp = await client.post(
            "/verdity/webhooks/github",
            content=body,
            headers={
                "X-GitHub-Event": "pull_request",
                "X-Hub-Signature-256": sig,
                "X-GitHub-Delivery": delivery_id,
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 202

        # Verify the delivery ID was persisted
        recent = await cache.load_recent()
        assert delivery_id in recent
    finally:
        await client.aclose()
        await cache.close()
        await app.state.queue.close()
        await app.state.audit.close()


@pytest.mark.asyncio
async def test_503_includes_retry_after_header(gateway_client_and_queue):
    """When the queue is unavailable, 503 must include Retry-After header."""
    client, queue = gateway_client_and_queue
    body = _make_signable_payload(SAMPLE_PAYLOAD)
    sig = _sign(body)
    delivery_id = str(uuid.uuid4())

    # Close the queue to simulate unavailability
    await queue.close()

    resp = await client.post(
        "/verdity/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sig,
            "X-GitHub-Delivery": delivery_id,
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 503
    assert "Retry-After" in resp.headers
    retry_after = int(resp.headers["Retry-After"])
    assert retry_after > 0
