"""
Test configuration and shared fixtures for Verdity.
"""

from __future__ import annotations

import os
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from verdity.audit_store import AuditStore
from verdity.event_queue import EventQueue
from verdity.semantic_index import SemanticIndex
from verdity.token_economics import TokenEconomicsService

# ── Temp DB directory (project-local .verdity-tests/) ─────────────────

_TEST_DB_DIR = os.path.join(os.path.dirname(__file__), ".verdity-tests")


@pytest.fixture(scope="session", autouse=True)
def _setup_test_env_and_tmpdir():
    """Ensure required env vars are set and create a temp DB directory."""
    os.makedirs(_TEST_DB_DIR, exist_ok=True)
    os.environ.setdefault("WEBHOOK_HMAC_SECRET", "test-hmac-secret-key-for-dev-only")
    os.environ.setdefault("WEBHOOK_HMAC_SECRET_PREVIOUS", "")
    os.environ.setdefault("GITHUB_APP_ID", "12345")
    os.environ.setdefault("GITHUB_APP_INSTALLATION_ID", "98765")
    os.environ.setdefault(
        "GITHUB_APP_PRIVATE_KEY",
        "-----BEGIN RSA PRIVATE KEY-----\\ntest\\n-----END RSA PRIVATE KEY-----",
    )
    yield
    # Cleanup temp DB files after all tests
    import shutil

    if os.path.isdir(_TEST_DB_DIR):
        shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)


def get_test_db_path(suffix: str = ".db") -> str:
    """Return a unique temp SQLite path for a test fixture."""
    import uuid

    return os.path.join(_TEST_DB_DIR, f"verdity-{uuid.uuid4().hex[:8]}{suffix}")


# ── Settings fixture ──────────────────────────────────────────────────


@pytest.fixture(scope="session")
def settings():
    """Return a test settings instance with temp-file backends."""
    from verdity.config import get_settings

    get_settings.cache_clear()
    return get_settings()


# ── Service fixtures (each gets an isolated temp DB) ──────────────────


@pytest_asyncio.fixture
async def queue() -> AsyncGenerator[EventQueue, None]:
    path = get_test_db_path("_queue.db")
    q = EventQueue(db_path=path)
    await q.connect()
    yield q
    await q.close()
    if os.path.exists(path):
        os.remove(path)


@pytest_asyncio.fixture
async def audit_store() -> AsyncGenerator[AuditStore, None]:
    path = get_test_db_path("_audit.db")
    store = AuditStore(db_path=path)
    await store.connect()
    yield store
    await store.close()
    if os.path.exists(path):
        os.remove(path)


@pytest_asyncio.fixture
async def token_economics() -> AsyncGenerator[TokenEconomicsService, None]:
    path = get_test_db_path("_te.db")
    svc = TokenEconomicsService(db_path=path)
    await svc.connect()
    yield svc
    await svc.close()
    if os.path.exists(path):
        os.remove(path)


@pytest_asyncio.fixture
async def semantic_index() -> AsyncGenerator[SemanticIndex, None]:
    path = get_test_db_path("_index.db")
    idx = SemanticIndex(db_path=path)
    await idx.connect()
    yield idx
    await idx.close()
    if os.path.exists(path):
        os.remove(path)


@pytest_asyncio.fixture
async def gateway_client(settings) -> AsyncGenerator[AsyncClient, None]:
    """Build an AsyncClient against the gateway app with test state initialized."""
    from verdity.gateway.app import DeliveryCache, _RateLimiter, app

    app.state.delivery_ids = set()
    app.state._delivery_cache_ts = {}
    app.state._last_eviction = 0.0
    app.state._rate_limiter = _RateLimiter()
    app.state.queue = EventQueue(db_path=get_test_db_path("_gwqueue.db"))
    app.state.audit = AuditStore(db_path=get_test_db_path("_gwaudit.db"))
    await app.state.queue.connect()
    await app.state.audit.connect()

    # Initialize persistent delivery cache
    delivery_cache = DeliveryCache(db_path=get_test_db_path("_delivery_cache.db"))
    await delivery_cache.connect()
    app.state._delivery_cache = delivery_cache
    # Load recent delivery IDs into memory
    recent_ids = await delivery_cache.load_recent()
    app.state.delivery_ids.update(recent_ids)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    await delivery_cache.close()
    await app.state.queue.close()
    await app.state.audit.close()
    for attr in ("queue", "audit"):
        db_path = getattr(app.state, attr)._db_path
        if db_path and os.path.exists(db_path):
            os.remove(db_path)
    delivery_db = delivery_cache._db_path
    if delivery_db and os.path.exists(delivery_db):
        os.remove(delivery_db)


# ── Services dict fixture (used by orchestrator tests) ────────────────


@pytest_asyncio.fixture
async def services(semantic_index, token_economics, audit_store) -> dict:
    """Return all service instances keyed by name."""
    return {
        "index": semantic_index,
        "token_economics": token_economics,
        "audit": audit_store,
    }
