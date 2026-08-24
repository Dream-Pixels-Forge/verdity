"""
Tests for the Ingestion Gateway endpoint.

Verifies:
  - 202 on valid signature → event queued
  - 401 on invalid signature → NOT queued
  - 409 on replay (duplicate delivery ID)
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

from verdity.hmac_verify import compute_signature
from verdity.event_queue import EventQueue


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
    from verdity.gateway.app import app
    from verdity.audit_store import AuditStore

    app.state.delivery_ids = set()
    app.state._delivery_cache_ts = {}
    app.state._last_eviction = 0.0
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
