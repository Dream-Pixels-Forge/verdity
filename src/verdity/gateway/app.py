"""
Ingestion Gateway — FastAPI application.

Receives GitHub webhook POSTs, verifies HMAC-SHA256 over raw body,
checks for replays, normalizes the payload, enqueues it, and returns 202.

Non-negotiable constraint #1 & #2:
  - HMAC verified BEFORE any parsing
  - No LLM calls, no semantic index hits — only verify + enqueue + ack
  - Decoupled from processing via the durable queue
Non-negotiable constraint #9:
  - Every accepted webhook is logged to the Audit Store

Security hardening (Phase 8):
  - Request body size limit enforced
  - Security headers on all responses
  - Delivery-ID dedupe cache with TTL-based eviction
  - Input sanitization on repo/file paths
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from verdity.audit_store import AuditStore
from verdity.config import get_settings
from verdity.event_queue import EventQueue
from verdity.hmac_verify import verify_with_rotation
from verdity.schemas import QueueEnvelope
from verdity.webhook_normalizer import normalize_webhook

logger = logging.getLogger(__name__)

# ── Security constants ────────────────────────────────────────────────

MAX_WEBHOOK_BODY_BYTES = 10 * 1024 * 1024  # 10 MiB — GitHub's practical limit
DELIVERY_CACHE_TTL_SECONDS = 24 * 3600    # 24 hours
_eviction_interval_seconds = 300           # evict every 5 minutes

# Sanitize file paths: reject absolute paths, path traversal, null bytes
_SAFE_PATH_RE = re.compile(r"^[\w\-./]+$")


def _sanitize_path(path: str) -> str:
    """Reject path traversal and non-printable chars in file paths."""
    if "\x00" in path:
        raise ValueError("Null byte in path")
    if not _SAFE_PATH_RE.match(path):
        raise ValueError(f"Invalid characters in path: {path!r}")
    if path.startswith("/") or path.startswith("\\"):
        raise ValueError("Absolute paths not allowed")
    if ".." in path.split("/"):
        raise ValueError("Path traversal not allowed")
    return path


def _add_security_headers(response: Response) -> None:
    """Attach defensive headers to every response."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Content-Security-Policy"] = "default-src 'none'"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-XSS-Protection"] = "1; mode=block"


def _cleanup_delivery_cache(state) -> None:
    """Remove expired entries from the delivery-ID cache."""
    now = time.time()
    expired = [k for k, ts in getattr(state, "_delivery_cache_ts", {}).items() if now - ts > DELIVERY_CACHE_TTL_SECONDS]
    for k in expired:
        state.delivery_ids.discard(k)
        state._delivery_cache_ts.pop(k, None)


# ── Lifespan ──────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown hooks for queue, audit store, and cache."""
    settings = get_settings()
    app.state.queue = EventQueue(db_path=settings.queue_sqlite_path)
    await app.state.queue.connect()
    app.state.audit = AuditStore(db_path=settings.audit_sqlite_path)
    await app.state.audit.connect()
    app.state.delivery_ids: set[str] = set()
    app.state._delivery_cache_ts: dict[str, float] = {}
    app.state._last_eviction: float = time.time()
    logger.info("Ingestion Gateway initialized")
    yield
    await app.state.queue.close()
    await app.state.audit.close()
    logger.info("Ingestion Gateway shut down")


app = FastAPI(
    title="Verdity Ingestion Gateway",
    description="Receives and verifies GitHub webhooks, enqueues for processing.",
    version="0.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Attach security headers and enforce body-size limit."""
    # Body size check — reject oversized payloads before parsing
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_WEBHOOK_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Payload too large"},
                )
        except ValueError:
            pass

    response = await call_next(request)
    _add_security_headers(response)
    return response


@app.post("/verdity/webhooks/github")
async def handle_github_webhook(
    request: Request,
    x_github_event: str = Header(..., alias="X-GitHub-Event"),
    x_hub_signature_256: str = Header(None, alias="X-Hub-Signature-256"),
    x_github_delivery: str = Header(..., alias="X-GitHub-Delivery"),
):
    """
    Main webhook endpoint.

    Response contract (API spec §1.4):
      202 — valid, queued
      401 — signature invalid/missing
      409 — duplicate delivery (replay)
      413 — payload too large
      503 — queue unreachable
    """
    # ── Evict stale cache entries periodically ──────────────────────
    now = time.time()
    last_eviction = getattr(request.app.state, "_last_eviction", 0.0)
    if now - last_eviction > _eviction_interval_seconds:
        _cleanup_delivery_cache(request.app.state)
        request.app.state._last_eviction = now

    # ── Step 1: Read raw body BEFORE any parsing ──────────────────────
    raw_body = await request.body()

    if len(raw_body) > MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    # ── Step 2: HMAC verification (CONSTANT-TIME, never == ) ──────────
    settings = get_settings()
    secret_current = settings.webhook_hmac_secret.get_secret_value().encode()
    secret_previous_raw = settings.webhook_hmac_secret_previous.get_secret_value()
    secret_previous = secret_previous_raw.encode() if secret_previous_raw else b""

    verified, matched = verify_with_rotation(
        secret_current=secret_current,
        secret_previous=secret_previous,
        raw_body=raw_body,
        signature_header=x_hub_signature_256 or "",
    )
    if not verified:
        logger.warning(
            "HMAC verification failed for delivery %s (matched=%s)",
            x_github_delivery, matched,
        )
        raise HTTPException(status_code=401, detail="Invalid or missing signature")

    # ── Step 3: Replay detection (delivery ID dedupe cache with TTL) ──
    if x_github_delivery in request.app.state.delivery_ids:
        logger.warning("Duplicate delivery ID detected: %s", x_github_delivery)
        raise HTTPException(status_code=409, detail="Duplicate delivery — already processed")
    request.app.state.delivery_ids.add(x_github_delivery)
    request.app.state._delivery_cache_ts[x_github_delivery] = time.time()

    # ── Step 4: Parse & normalize payload (only after verification passes) ─
    try:
        payload = await request.json()
    except Exception as exc:
        logger.error("Failed to parse webhook JSON: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    try:
        event = normalize_webhook(
            event_name=x_github_event,
            action=payload.get("action"),
            delivery_id=x_github_delivery,
            payload=payload,
        )
    except Exception as exc:
        logger.error("Failed to normalize webhook: %s", exc)
        raise HTTPException(status_code=400, detail="Webhook normalization failed") from exc

    # ── Step 5: Sanitize paths before enqueueing ──────────────────────
    pr = event.pull_request
    if pr:
        try:
            pr.head_sha = _sanitize_path(pr.head_sha)
            pr.base_sha = _sanitize_path(pr.base_sha)
        except ValueError as exc:
            logger.warning("Rejected suspicious PR ref: %s", exc)
            raise HTTPException(status_code=400, detail="Invalid PR reference")

    # ── Step 6: Enqueue (decoupling boundary — nothing else happens here) ─
    envelope = QueueEnvelope(event=event)
    try:
        msg_id = await request.app.state.queue.publish(envelope)
    except Exception as exc:
        logger.error("Queue publish failed: %s", exc)
        raise HTTPException(status_code=503, detail="Queue unavailable")

    # ── Step 7: Audit log (constraint #9 — if it isn't logged, it didn't happen) ─
    await request.app.state.audit.append(
        event_type="webhook.ingested",
        entity_type="delivery",
        entity_id=x_github_delivery,
        payload={
            "trigger_type": event.trigger_type.value,
            "repo": f"{event.repo.owner}/{event.repo.name}",
            "pr_number": event.pull_request.number if event.pull_request else None,
            "message_id": msg_id,
            "hmac_matched_secret": matched,
        },
    )

    logger.info(
        "Webhook accepted: delivery=%s event=%s repo=%s/%s pr=%s",
        x_github_delivery, event.trigger_type, event.repo.owner, event.repo.name,
        event.pull_request.number if event.pull_request else None,
    )

    return JSONResponse(
        status_code=202,
        content={"delivery_id": x_github_delivery, "status": "queued", "message_id": msg_id},
    )


@app.get("/verdity/health")
async def health():
    """Liveness/readiness probe."""
    return {"status": "ok", "service": "verdity-gateway"}
