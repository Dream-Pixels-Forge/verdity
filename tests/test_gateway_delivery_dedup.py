"""SG-07 — record a delivery ID only once the webhook is safely enqueued.

At-least-once ingestion: if the gateway can't enqueue (503) or rejects the
request (400), the provider's redelivery with the same ID must be accepted,
not refused as a 409 duplicate. Two concurrent requests with one ID must still
enqueue exactly once.

The fixture here deliberately does NOT patch ``RepoRef`` (unlike
``test_gateway_extra.gw_client``), so the platform route runs exactly as in
production.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import pathlib
import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from verdity.audit_store import AuditStore
from verdity.event_queue import EventQueue

GITHUB_SECRET = "test-hmac-secret-key-for-dev-only"
GITLAB_SECRET = "gitlab-secret"

GITHUB_PR_OPENED = {
    "action": "opened",
    "number": 7,
    "pull_request": {
        "number": 7,
        "head": {"sha": "a1b2c3d4e5f6"},
        "base": {"sha": "f6e5d4c3b2a1"},
        "draft": False,
        "title": "t",
    },
    "repository": {
        "id": 1,
        "name": "widgets",
        "full_name": "acme/widgets",
        "owner": {"login": "acme"},
    },
    "sender": {"login": "dev"},
}
GITLAB_MR_OPEN = (pathlib.Path(__file__).parent / "fixtures" / "gitlab_mr_open.json").read_bytes()


def _github_headers(body: bytes, delivery_id: str, event: str = "pull_request") -> dict:
    sig = "sha256=" + hmac.new(GITHUB_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": sig,
        "X-GitHub-Delivery": delivery_id,
        "Content-Type": "application/json",
    }


def _gitlab_headers(delivery_id: str) -> dict:
    return {
        "Content-Type": "application/json",
        "X-Gitlab-Token": GITLAB_SECRET,
        "X-Gitlab-Event-UUID": delivery_id,
    }


def _body(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


@pytest_asyncio.fixture
async def gw(monkeypatch) -> AsyncGenerator[tuple[AsyncClient, object], None]:
    from verdity.config import get_settings
    from verdity.gateway.app import DeliveryCache, _parse_ip_allowlist, _RateLimiter, app

    monkeypatch.setenv("WEBHOOK_HMAC_SECRET", GITHUB_SECRET)
    monkeypatch.setenv("WEBHOOK_HMAC_SECRET_PREVIOUS", "")
    monkeypatch.setenv("GITLAB_WEBHOOK_SECRET", GITLAB_SECRET)
    get_settings.cache_clear()

    app.state.delivery_ids = set()
    app.state._delivery_cache_ts = {}
    app.state._last_eviction = 0.0
    app.state._rate_limiter = _RateLimiter()
    app.state._github_ip_allowlist = _parse_ip_allowlist("")
    app.state._delivery_cache = DeliveryCache(db_path=":memory:")
    app.state.queue = EventQueue(db_path=":memory:")
    app.state.audit = AuditStore(db_path=":memory:")
    for store in (app.state._delivery_cache, app.state.queue, app.state.audit):
        await store.connect()

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        yield client, app.state
    finally:
        await client.aclose()
        for store in (app.state._delivery_cache, app.state.queue, app.state.audit):
            await store.close()
        get_settings.cache_clear()


def _fail_publish_once(state, monkeypatch) -> None:
    real = state.queue.publish
    calls = {"n": 0}

    async def flaky(envelope):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        return await real(envelope)

    monkeypatch.setattr(state.queue, "publish", flaky)


# ── Retry after a failed enqueue is accepted ──────────────────────────


@pytest.mark.asyncio
async def test_github_retry_after_503_is_accepted(gw, monkeypatch):
    client, state = gw
    _fail_publish_once(state, monkeypatch)
    body, delivery = _body(GITHUB_PR_OPENED), str(uuid.uuid4())

    first = await client.post(
        "/verdity/webhooks/github", content=body, headers=_github_headers(body, delivery)
    )
    assert first.status_code == 503
    assert delivery not in state.delivery_ids
    assert delivery not in await state._delivery_cache.load_recent()

    retry = await client.post(
        "/verdity/webhooks/github", content=body, headers=_github_headers(body, delivery)
    )
    assert retry.status_code == 202, retry.text
    assert retry.json()["status"] == "queued"
    assert (await state.queue.count_by_state())["pending"] == 1

    # Once accepted, the ID is a duplicate, in memory and persistently.
    again = await client.post(
        "/verdity/webhooks/github", content=body, headers=_github_headers(body, delivery)
    )
    assert again.status_code == 409
    assert delivery in await state._delivery_cache.load_recent()


@pytest.mark.asyncio
async def test_platform_retry_after_503_is_accepted(gw, monkeypatch):
    client, state = gw
    _fail_publish_once(state, monkeypatch)
    delivery = "gl-" + str(uuid.uuid4())

    first = await client.post(
        "/verdity/webhooks/gitlab", content=GITLAB_MR_OPEN, headers=_gitlab_headers(delivery)
    )
    assert first.status_code == 503
    assert delivery not in state.delivery_ids

    retry = await client.post(
        "/verdity/webhooks/gitlab", content=GITLAB_MR_OPEN, headers=_gitlab_headers(delivery)
    )
    assert retry.status_code == 202, retry.text
    assert (await state.queue.count_by_state())["pending"] == 1

    again = await client.post(
        "/verdity/webhooks/gitlab", content=GITLAB_MR_OPEN, headers=_gitlab_headers(delivery)
    )
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_github_400_does_not_burn_the_delivery_id(gw):
    client, state = gw
    delivery = str(uuid.uuid4())

    bad = b"{not json"
    resp = await client.post(
        "/verdity/webhooks/github", content=bad, headers=_github_headers(bad, delivery)
    )
    assert resp.status_code == 400

    good = _body(GITHUB_PR_OPENED)
    resp = await client.post(
        "/verdity/webhooks/github", content=good, headers=_github_headers(good, delivery)
    )
    assert resp.status_code == 202, resp.text


@pytest.mark.asyncio
async def test_ignored_event_still_counts_as_delivered(gw):
    client, state = gw
    body = _body({**GITHUB_PR_OPENED, "action": "closed"})
    delivery = str(uuid.uuid4())

    first = await client.post(
        "/verdity/webhooks/github", content=body, headers=_github_headers(body, delivery)
    )
    assert first.json()["status"] == "ignored"

    again = await client.post(
        "/verdity/webhooks/github", content=body, headers=_github_headers(body, delivery)
    )
    assert again.status_code == 409


# ── Concurrent duplicates enqueue once ────────────────────────────────


async def _concurrent(state, monkeypatch, send):
    """Hold the first publish open until the second request has been answered."""
    real = state.queue.publish
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(envelope):
        entered.set()
        await release.wait()
        return await real(envelope)

    monkeypatch.setattr(state.queue, "publish", slow)

    first = asyncio.create_task(send())
    # First request is inside publish, its ID not yet committed. Time out
    # (rather than hang) if the route never reaches publish.
    entered_or_done = asyncio.create_task(entered.wait())
    await asyncio.wait({entered_or_done, first}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
    if not entered.is_set():
        entered_or_done.cancel()
        resp = await first
        pytest.fail(f"request never reached publish: {resp.status_code} {resp.text}")
    try:
        # Without the in-flight reservation the duplicate would block in
        # publish too, so bound it: a regression fails instead of hanging.
        second = await asyncio.wait_for(send(), timeout=5)
    finally:
        release.set()
    return await first, second


@pytest.mark.asyncio
async def test_github_concurrent_duplicates_enqueue_once(gw, monkeypatch):
    client, state = gw
    body, delivery = _body(GITHUB_PR_OPENED), str(uuid.uuid4())

    first, second = await _concurrent(
        state,
        monkeypatch,
        lambda: client.post(
            "/verdity/webhooks/github", content=body, headers=_github_headers(body, delivery)
        ),
    )

    assert sorted([first.status_code, second.status_code]) == [202, 409]
    assert (await state.queue.count_by_state())["pending"] == 1
    assert not getattr(state, "delivery_inflight", set())  # reservation released


@pytest.mark.asyncio
async def test_platform_concurrent_duplicates_enqueue_once(gw, monkeypatch):
    client, state = gw
    delivery = "gl-" + str(uuid.uuid4())

    first, second = await _concurrent(
        state,
        monkeypatch,
        lambda: client.post(
            "/verdity/webhooks/gitlab", content=GITLAB_MR_OPEN, headers=_gitlab_headers(delivery)
        ),
    )

    assert sorted([first.status_code, second.status_code]) == [202, 409]
    assert (await state.queue.count_by_state())["pending"] == 1


# ── Platform route works without test-only RepoRef patching ───────────


@pytest.mark.asyncio
async def test_gitlab_mr_is_queued_without_repo_id_patch(gw):
    """RepoRef.id is required; the platform route must supply it."""
    client, state = gw
    resp = await client.post(
        "/verdity/webhooks/gitlab",
        content=GITLAB_MR_OPEN,
        headers=_gitlab_headers(str(uuid.uuid4())),
    )

    assert resp.status_code == 202, resp.text
    queued = await state.queue.consume()
    project_id = json.loads(GITLAB_MR_OPEN)["project"]["id"]
    assert queued.event.repo.id == project_id
