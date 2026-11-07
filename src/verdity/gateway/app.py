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

Security hardening (Phase 12 - Issue #41):
  - GitHub IP allowlist middleware on /webhook endpoint (config-gated via GITHUB_WEBHOOK_IPS)
  - Redis-backed rate limiter (redis.asyncio) behind feature flag
  - HTTPX timeout config (10s total, 5s connect) on all clients
  - Webhook source validation: X-Hub-Signature-256 + source IP in allowlist
  - Trusted-proxy client IP: X-Forwarded-For only honored from trusted proxies
  - Prompt-injection classifier for security agent
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import re
import time
from collections import defaultdict
from contextlib import suppress
from ipaddress import ip_address
from typing import Protocol

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from verdity.async_sqlite import AsyncConnection
from verdity.audit_store import AuditStore
from verdity.config import get_settings
from verdity.event_queue import EventQueue
from verdity.hmac_verify import verify_with_rotation
from verdity.metrics_store import MetricsStore
from verdity.schemas import QueueEnvelope
from verdity.webhook_normalizer import normalize_webhook

logger = logging.getLogger(__name__)

# ── Security constants ────────────────────────────────────────────────
MAX_WEBHOOK_BODY_BYTES = 10 * 1024 * 1024  # 10 MiB — GitHub's practical limit
DELIVERY_CACHE_TTL_SECONDS = 24 * 3600  # 24 hours
_eviction_interval_seconds = 300  # evict every 5 minutes
RATE_LIMIT_MAX_REQUESTS = 100  # per IP per window
RATE_LIMIT_WINDOW_SECONDS = 60  # sliding window in seconds


class _RateLimiterProtocol(Protocol):
    """Protocol for rate limiter types (sync or async).

    Both in-memory and Redis limiters must support async is_allowed()
    for use in the middleware.
    """

    async def is_allowed(self, request: Request) -> tuple[bool, float]:
        """Check if the request is within the rate limit.

        Returns (allowed, retry_after_seconds).
        """
        ...


# Default GitHub webhook IP ranges (from GitHub's meta API)
# https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries#validating-webhook-deliveries
DEFAULT_GITHUB_WEBHOOK_IPS = (
    "192.30.252.0/22,"
    "185.199.108.0/22,"
    "140.82.112.0/20,"
    "143.55.64.0/20,"
    "40.126.0.0/16,"
    "20.201.28.152/29,"
    "20.207.73.82/29"
)


def _parse_ip_allowlist(cidr_string: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parse comma-separated CIDR string into list of IP networks.

    Args:
        cidr_string: Comma-separated CIDR notations (e.g., "192.168.1.0/24,10.0.0.1")

    Returns:
        List of IPv4Network or IPv6Network objects. Empty list if input is empty.
    """
    if not cidr_string or not cidr_string.strip():
        return []

    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for part in cidr_string.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            # Try parsing as network (CIDR)
            network = ipaddress.ip_network(part, strict=False)
            networks.append(network)
        except ValueError:
            logger.warning("Invalid IP/CIDR in allowlist: %s", part)
            continue
    return networks


def _is_ip_allowed(
    client_ip: str, allowed_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network]
) -> bool:
    """Check if client IP is in any of the allowed networks."""
    if not allowed_networks:
        return True  # No allowlist configured = allow all

    try:
        # Convert client IP string to address object for ipaddress containment check
        addr = ipaddress.ip_address(client_ip)
        return any(addr in network for network in allowed_networks)
    except ValueError:
        return False


def client_ip(request: Request, trusted_proxies: list) -> str:
    """Derive the client IP safely, honoring X-Forwarded-For only from trusted proxies.

    Security policy:
    - If trusted_proxies is empty: never trust XFF; return request.client.host.
    - If the direct connection peer is NOT in trusted_proxies: return request.client.host
      (the proxy's IP, NOT the original client — XFF is ignored to prevent spoofing).
    - If the peer IS in trusted_proxies: walk XFF from the rightmost entry; return the
      first IP that is NOT itself a trusted proxy. That is the real original client.
    - If no XFF header: return request.client.host.

    This prevents both:
    1. Allowlist bypass: sending X-Forwarded-For: spoofed_ip from any host
    2. Rate-limit bypass: changing XFF on each request to get a fresh bucket
    """
    forwarded = request.headers.get("x-forwarded-for")

    if not trusted_proxies:
        # No trusted proxies configured — never trust XFF
        return request.client.host if request.client else "unknown"

    if not forwarded:
        # No XFF header
        return request.client.host if request.client else "unknown"

    # Check if the direct peer is trusted
    peer_host = request.client.host if request.client else None
    if not peer_host:
        return "unknown"

    try:
        peer_ip = ip_address(peer_host)
        peer_is_trusted = any(peer_ip in network for network in trusted_proxies)
    except ValueError:
        # Can't parse peer IP — don't trust XFF
        return peer_host

    if not peer_is_trusted:
        # Peer is not a trusted proxy — never trust XFF, return peer IP
        return peer_host

    # Peer is trusted — walk XFF from right to left; return the first untrusted hop
    ips = [ip.strip() for ip in forwarded.split(",")]

    for ip_str in reversed(ips):
        try:
            ip = ip_address(ip_str)
            # If this IP is NOT a trusted proxy, it's the real original client
            if not any(ip in network for network in trusted_proxies):
                return ip_str
        except ValueError:
            continue

    # All IPs in XFF are trusted proxies, or we couldn't parse them
    # Return the leftmost (original client in XFF convention)
    return ips[0] if ips else peer_host


# Lua script for sliding window rate limiting in Redis
# Uses sorted set with timestamps as scores for precise sliding window
_REDIS_RATE_LIMIT_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

-- Remove expired entries
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)

-- Count current requests in window
local count = redis.call('ZCARD', key)

if count >= limit then
    -- Rate limited: return 0 and TTL of oldest entry
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    if #oldest > 0 then
        local retry_after = (oldest[2] + window) - now
        return {0, math.ceil(retry_after)}
    end
    return {0, ttl}
end

-- Add current request
local member = now .. '-' .. math.random(1000000)
redis.call('ZADD', key, now, member)
redis.call('EXPIRE', key, ttl)
return {1, 0}
"""


class _RateLimiter:
    """In-memory sliding-window rate limiter, per client IP.

    Tracks timestamps of recent requests and rejects when the count
    within the window exceeds the limit. Returns the number of seconds
    the caller should retry after.
    """

    def __init__(
        self,
        max_requests: int = RATE_LIMIT_MAX_REQUESTS,
        window_seconds: float = RATE_LIMIT_WINDOW_SECONDS,
    ) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def _evict(self, timestamps: list[float], now: float) -> None:
        """Remove timestamps older than the window."""
        cutoff = now - self._window
        while timestamps and timestamps[0] < cutoff:
            timestamps.pop(0)

    async def is_allowed(self, request: Request) -> tuple[bool, float]:
        """Check if the request is within the rate limit.

        Returns (allowed, retry_after_seconds).
        """
        now = time.time()
        ip = client_ip(request, [])  # In-memory limiter always ignores XFF
        timestamps = self._buckets[ip]
        self._evict(timestamps, now)
        if len(timestamps) >= self._max:
            oldest = timestamps[0]
            retry_after = self._window - (now - oldest)
            return False, max(retry_after, 1.0)
        timestamps.append(now)
        return True, 0.0


# Redis-backed rate limiter for distributed deployments
class RedisRateLimiter:
    """Redis-backed sliding-window rate limiter for distributed deployments.

    Uses a sorted set in Redis to track request timestamps per client IP.
    Implements sliding window algorithm via atomic Lua script.
    Falls back to in-memory limiter on Redis errors.

    Usage:
        limiter = RedisRateLimiter(redis_url="redis://localhost:6379/0")
        await limiter.connect()
        allowed, retry_after = await limiter.is_allowed(request)
        await limiter.close()
    """

    def __init__(
        self,
        redis_url: str,
        max_requests: int = RATE_LIMIT_MAX_REQUESTS,
        window_seconds: float = RATE_LIMIT_WINDOW_SECONDS,
    ) -> None:
        self._redis_url = redis_url
        self._max = max_requests
        self._window = window_seconds
        self._ttl = int(window_seconds) + 1  # TTL slightly longer than window
        self._redis = None
        self._script_sha = None
        self._fallback_limiter = _RateLimiter(max_requests, window_seconds)
        self._connected = False

    async def connect(self) -> None:
        """Establish Redis connection and load Lua script."""
        import redis.asyncio as redis

        self._redis = redis.from_url(
            self._redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5.0,
            socket_timeout=5.0,
        )
        # Test connection
        await self._redis.ping()
        # Load Lua script
        self._script_sha = await self._redis.script_load(_REDIS_RATE_LIMIT_SCRIPT)
        self._connected = True
        logger.info("Redis rate limiter connected to %s", self._redis_url)

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._redis = None
            self._connected = False

    async def is_allowed(self, request: Request) -> tuple[bool, float]:
        """Check if the request is within the rate limit.

        Falls back to in-memory limiter on Redis errors.
        """
        if not self._connected or self._redis is None:
            logger.warning("Redis rate limiter not connected, using fallback")
            return await self._fallback_limiter.is_allowed(request)

        ip = client_ip(request, [])  # When behind no trusted proxy, ignore XFF
        key = f"rate_limit:{ip}"
        now = time.time()

        try:
            # Execute Lua script atomically
            result = await self._redis.evalsha(
                self._script_sha,
                1,  # number of keys
                key,
                str(now),
                str(self._window),
                str(self._max),
                str(self._ttl),
            )
            allowed = bool(result[0])
            retry_after = float(result[1])
            return allowed, retry_after
        except Exception as exc:
            logger.warning("Redis rate limiter error, falling back: %s", exc)
            # Mark as disconnected for auto-reconnect; fallbackLimiter is async
            self._connected = False
            return await self._fallback_limiter.is_allowed(request)


class DeliveryCache:
    """Persistent delivery-ID dedup cache backed by SQLite.

    On startup, loads recent delivery IDs into the in-memory set.
    On each new delivery ID, persists it alongside adding to the in-memory set.
    Evicts expired entries periodically.
    """

    CREATE_TABLE_SQL = """
        CREATE TABLE IF NOT EXISTS delivery_cache (
            delivery_id TEXT PRIMARY KEY,
            created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );
        CREATE INDEX IF NOT EXISTS idx_delivery_created ON delivery_cache(created_at);
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn: AsyncConnection | None = None

    async def connect(self) -> None:
        self._conn = AsyncConnection(self._db_path)
        await self._conn.connect()
        await self._conn.executescript(self.CREATE_TABLE_SQL)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def add(self, delivery_id: str) -> None:
        """Persist a delivery ID. Idempotent — INSERT OR IGNORE."""
        if self._conn is None:
            raise RuntimeError("DeliveryCache is not connected. Call connect() first.")
        await self._conn.execute(
            "INSERT OR IGNORE INTO delivery_cache (delivery_id) VALUES (?)",
            (delivery_id,),
        )
        await self._conn.commit()

    async def load_recent(self) -> set[str]:
        """Load delivery IDs that haven't expired yet."""
        if self._conn is None:
            raise RuntimeError("DeliveryCache is not connected. Call connect() first.")
        cutoff_epoch = time.time() - DELIVERY_CACHE_TTL_SECONDS
        cutoff_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff_epoch))
        rows = await self._conn.execute(
            "SELECT delivery_id FROM delivery_cache WHERE created_at >= ?",
            (cutoff_iso,),
        )
        return {row["delivery_id"] for row in rows}

    async def evict_expired(self) -> int:
        """Remove expired entries. Returns the number of rows deleted."""
        if self._conn is None:
            raise RuntimeError("DeliveryCache is not connected. Call connect() first.")
        cutoff_epoch = time.time() - DELIVERY_CACHE_TTL_SECONDS
        cutoff_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff_epoch))
        rows = await self._conn.execute(
            "DELETE FROM delivery_cache WHERE created_at < ?",
            (cutoff_iso,),
        )
        await self._conn.commit()
        return len(rows)


# Sanitize file paths: reject absolute paths, path traversal, null bytes
_SAFE_PATH_RE = re.compile(r"^[\w\-./]+$")


def _sanitize_path(path: str) -> str:
    """Reject path traversal and non-printable chars in file paths."""
    if "\x00" in path:
        raise ValueError("Null byte in path")
    if not _SAFE_PATH_RE.match(path):
        raise ValueError(f"Invalid characters in path: {path!r}")
    if path.startswith(("/", "\\")):
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
    """Remove expired entries from the in-memory delivery-ID cache."""
    now = time.time()
    expired = [
        k
        for k, ts in getattr(state, "_delivery_cache_ts", {}).items()
        if now - ts > DELIVERY_CACHE_TTL_SECONDS
    ]
    for k in expired:
        state.delivery_ids.discard(k)
        state._delivery_cache_ts.pop(k, None)


# ── Lifespan ──────────────────────────────────────────────────────────


async def lifespan(app: FastAPI):
    """Startup/shutdown hooks for queue, audit store, and cache."""
    settings = get_settings()
    app.state.queue = EventQueue(db_path=settings.queue_sqlite_path)
    await app.state.queue.connect()
    app.state.audit = AuditStore(db_path=settings.audit_sqlite_path)
    await app.state.audit.connect()

    # Initialize rate limiter (Redis-backed if enabled, otherwise in-memory)
    if getattr(settings, "redis_rate_limiter_enabled", False):
        redis_url = getattr(settings, "redis_url", "redis://localhost:6379/0")
        app.state._rate_limiter = RedisRateLimiter(
            redis_url=redis_url,
            max_requests=RATE_LIMIT_MAX_REQUESTS,
            window_seconds=RATE_LIMIT_WINDOW_SECONDS,
        )
        await app.state._rate_limiter.connect()
        logger.info("Redis-backed rate limiter enabled")
    else:
        app.state._rate_limiter = _RateLimiter()
        logger.info("In-memory rate limiter enabled")

    # Persistent delivery-ID cache
    delivery_cache_path = getattr(settings, "delivery_cache_sqlite_path", None)
    if delivery_cache_path is None:
        # Derive from audit path as a sensible default
        import pathlib

        _base = pathlib.Path(settings.audit_sqlite_path).parent
        delivery_cache_path = str(_base / "delivery_cache.db")
    app.state._delivery_cache = DeliveryCache(db_path=delivery_cache_path)
    await app.state._delivery_cache.connect()

    # Load persisted delivery IDs into memory
    app.state.delivery_ids: set[str] = await app.state._delivery_cache.load_recent()
    app.state._delivery_cache_ts: dict[str, float] = {}
    app.state._last_eviction: float = time.time()

    # Load IP allowlist for GitHub webhooks (Issue #41)
    # Config-gated via GITHUB_WEBHOOK_IPS env var
    github_webhook_ips = getattr(settings, "github_webhook_ips", "")
    if not github_webhook_ips:
        # Use default GitHub webhook IP ranges if not explicitly configured
        github_webhook_ips = DEFAULT_GITHUB_WEBHOOK_IPS
    app.state._github_ip_allowlist = _parse_ip_allowlist(github_webhook_ips)
    if app.state._github_ip_allowlist:
        logger.info(
            "GitHub IP allowlist initialized with %d network(s)",
            len(app.state._github_ip_allowlist),
        )
    else:
        logger.info("GitHub IP allowlist disabled (no networks configured)")

    # Trusted proxy CIDRs for XFF handling
    trusted_proxies_cidrs = getattr(settings, "trusted_proxies", "")
    if trusted_proxies_cidrs:
        app.state._trusted_proxies = _parse_ip_allowlist(trusted_proxies_cidrs)
        logger.info(
            "Trusted proxies initialized with %d network(s)",
            len(app.state._trusted_proxies),
        )
    else:
        app.state._trusted_proxies = []
        logger.info("Trusted proxies not configured — XFF will not be honored")

    logger.info(
        "Ingestion Gateway initialized (loaded %d cached delivery IDs)",
        len(app.state.delivery_ids),
    )
    yield
    # Evict expired entries from persistent cache before closing
    with suppress(OSError):
        await app.state._delivery_cache.evict_expired()
    await app.state._delivery_cache.close()
    await app.state.queue.close()
    await app.state.audit.close()
    metrics: MetricsStore | None = getattr(app.state, "metrics", None)
    if metrics is not None:
        await metrics.close()
    logger.info("Ingestion Gateway shut down")
    # Close rate limiter if it has a close method (RedisRateLimiter)
    rate_limiter = getattr(app.state, "_rate_limiter", None)
    if rate_limiter and hasattr(rate_limiter, "close"):
        await rate_limiter.close()


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


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Rate-limit POST /verdity/webhooks/github by client IP."""
    if request.method == "POST" and request.url.path == "/verdity/webhooks/github":
        limiter: _RateLimiterProtocol | None = getattr(request.app.state, "_rate_limiter", None)
        if limiter is not None:
            allowed, retry_after = await limiter.is_allowed(request)
            if not allowed:
                response = JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded. Try again later."},
                )
                response.headers["Retry-After"] = str(int(retry_after))
                _add_security_headers(response)
                return response
    return await call_next(request)


@app.middleware("http")
async def ip_allowlist_middleware(request: Request, call_next):
    """IP allowlist for GitHub webhook endpoint (config-gated via GITHUB_WEBHOOK_IPS).

    Only applies to POST /verdity/webhooks/github.
    When allowlist is empty (feature disabled), all IPs are allowed.
    """
    if request.method == "POST" and request.url.path == "/verdity/webhooks/github":
        allowed_networks: list = getattr(request.app.state, "_github_ip_allowlist", [])
        if allowed_networks:  # Only enforce when allowlist is configured
            # Extract client IP safely — XFF only honored from trusted proxies
            trusted_proxies = getattr(request.app.state, "_trusted_proxies", [])
            client_ip_val = client_ip(request, trusted_proxies)

            if not _is_ip_allowed(client_ip_val, allowed_networks):
                logger.warning(
                    "IP allowlist rejected request from %s to /verdity/webhooks/github",
                    client_ip_val,
                )
                response = JSONResponse(
                    status_code=403,
                    content={"detail": "IP not allowed"},
                )
                _add_security_headers(response)
                return response
    return await call_next(request)


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
            x_github_delivery,
            matched,
        )
        raise HTTPException(status_code=401, detail="Invalid or missing signature")

    # ── Step 3: Replay detection (delivery ID dedupe cache with TTL) ──
    if x_github_delivery in request.app.state.delivery_ids:
        logger.warning("Duplicate delivery ID detected: %s", x_github_delivery)
        raise HTTPException(status_code=409, detail="Duplicate delivery — already processed")
    request.app.state.delivery_ids.add(x_github_delivery)
    request.app.state._delivery_cache_ts[x_github_delivery] = time.time()
    # Persist to SQLite so dedup survives restart
    delivery_cache: DeliveryCache | None = getattr(request.app.state, "_delivery_cache", None)
    if delivery_cache is not None:
        await delivery_cache.add(x_github_delivery)

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

    # ── Step 5: Check if trigger is reviewable ──────────────────────────
    # Known GitHub event types that we explicitly support for PR reviews
    KNOWN_GITHUB_EVENT_TYPES = {
        "pull_request",
        "pull_request_review_comment",
        "check_suite",
        "push",
        "installation",
        "installation_repositories",
    }

    # Reviewable trigger types that should be enqueued for review
    REVIEWABLE_TRIGGERS = {
        "pr.opened",
        "pr.synchronize",
        "pr.reopened",
        "pr.ready_for_review",
        "review_comment.created",
        "check_suite.rerequested",
        "push",
        "installation.created",
        "installation_repositories.added",
        "installation.deleted",
    }

    trigger_type_str = event.trigger_type.value

    # Check if the event type is known and the trigger is reviewable
    is_known_event = x_github_event in KNOWN_GITHUB_EVENT_TYPES
    is_reviewable = trigger_type_str in REVIEWABLE_TRIGGERS

    if not is_known_event or not is_reviewable:
        logger.info(
            "Ignoring non-reviewable GitHub event: event=%s trigger=%s delivery=%s known=%s",
            x_github_event,
            trigger_type_str,
            x_github_delivery,
            is_known_event,
        )
        await request.app.state.audit.append(
            event_type="webhook.ignored",
            entity_type="delivery",
            entity_id=x_github_delivery,
            payload={
                "platform": "github",
                "trigger_type": trigger_type_str,
                "event": x_github_event,
                "action": payload.get("action", ""),
                "is_known_event": is_known_event,
            },
        )
        return JSONResponse(
            status_code=202,
            content={
                "status": "ignored",
                "delivery_id": x_github_delivery,
                "platform": "github",
                "trigger_type": trigger_type_str,
                "message": f"Event type not reviewable: {trigger_type_str}",
            },
        )

    # ── Step 7: Sanitize paths before enqueueing ──────────────────────
    # Only sanitize if we have valid non-empty PR SHAs (non-PR events may have empty ones)
    pr = event.pull_request
    if pr and pr.head_sha and pr.base_sha:
        try:
            pr.head_sha = _sanitize_path(pr.head_sha)
            pr.base_sha = _sanitize_path(pr.base_sha)
        except ValueError as exc:
            logger.warning("Rejected suspicious PR ref: %s", exc)
            raise HTTPException(status_code=400, detail="Invalid PR reference") from exc

    # ── Step 8: Enqueue (decoupling boundary — nothing else happens here) ─
    envelope = QueueEnvelope(event=event)
    try:
        msg_id = await request.app.state.queue.publish(envelope)
    except (OSError, RuntimeError) as exc:
        logger.error("Queue publish failed: %s", exc)
        response = JSONResponse(
            status_code=503,
            content={"detail": "Queue unavailable"},
        )
        response.headers["Retry-After"] = "30"
        _add_security_headers(response)
        return response

    # ── Step 9: Audit log (constraint #9 — if it isn't logged, it didn't happen) ─
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
        x_github_delivery,
        event.trigger_type,
        event.repo.owner,
        event.repo.name,
        event.pull_request.number if event.pull_request else None,
    )

    return JSONResponse(
        status_code=202,
        content={"delivery_id": x_github_delivery, "status": "queued", "message_id": msg_id},
    )


# ── Unified Multi-Platform Webhook Endpoint (Phase 13) ────────────────


@app.post("/verdity/webhooks/{platform}")
async def handle_platform_webhook(
    platform: str,
    request: Request,
):
    """
    Unified webhook endpoint for all supported platforms.

    Path params:
      platform: "github", "gitlab", or "bitbucket"

    Each platform uses its own verification mechanism:
      - GitHub: X-Hub-Signature-256 (HMAC-SHA256)
      - GitLab: X-Gitlab-Token (shared secret comparison)
      - Bitbucket: X-Hub-Signature (HMAC-SHA256)

    Response contract:
      202 — valid, queued
      400 — unknown platform
      401 — signature invalid/missing
      409 — duplicate delivery (replay)
      413 — payload too large
      503 — queue unreachable
    """
    from verdity.platforms import BitbucketPlatform, GitHubPlatform, GitLabPlatform

    # ── Step 0: Validate platform ────────────────────────────────────
    platform_map = {
        "github": GitHubPlatform,
        "gitlab": GitLabPlatform,
        "bitbucket": BitbucketPlatform,
    }
    platform_cls = platform_map.get(platform)
    if platform_cls is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported platform: {platform}. Supported: {', '.join(platform_map)}",
        )

    platform_instance = platform_cls()

    # ── Step 1: Read raw body BEFORE any parsing ──────────────────────
    raw_body = await request.body()

    if len(raw_body) > MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    # ── Step 2: Platform-native verification ──────────────────────────
    # Note: the `github` branch below is unreachable in practice — the
    # dedicated @app.post("/verdity/webhooks/github") route is registered first
    # and matches before this catch-all. It is kept so the route stays correct
    # if the dedicated handler is ever removed or reordered.
    settings = get_settings()
    if platform == "github":  # pragma: no cover - shadowed by the dedicated route
        secret = settings.webhook_hmac_secret.get_secret_value()
    elif platform == "gitlab":
        secret = settings.gitlab_webhook_secret.get_secret_value()
    elif platform == "bitbucket":
        secret = settings.bitbucket_webhook_secret.get_secret_value()
    else:  # pragma: no cover - platform_map already rejected unknown values
        secret = ""

    if not secret:
        logger.warning("No webhook secret configured for platform: %s", platform)
        raise HTTPException(status_code=401, detail=f"No secret configured for {platform}")

    headers_dict = {k.lower(): v for k, v in request.headers.items()}
    if not platform_instance.verify_webhook(headers_dict, raw_body, secret):
        delivery_id = headers_dict.get(
            "x-github-delivery",
            headers_dict.get("x-request-uuid", "unknown"),
        )
        logger.warning(
            "Webhook verification failed for platform=%s delivery=%s",
            platform,
            delivery_id,
        )
        raise HTTPException(status_code=401, detail="Invalid or missing signature")

    # ── Step 3: Parse payload ─────────────────────────────────────────
    try:
        payload = await request.json()
    except Exception as exc:
        logger.error("Failed to parse webhook JSON for %s: %s", platform, exc)
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    # ── Step 4: Normalize event ───────────────────────────────────────
    try:
        event_dict = platform_instance.normalize_event(headers_dict, payload)
    except Exception as exc:
        logger.error("Failed to normalize %s webhook: %s", platform, exc)
        raise HTTPException(status_code=400, detail="Webhook normalization failed") from exc

    # ── Step 5: Replay detection ──────────────────────────────────────
    delivery_id = event_dict.get("delivery_id", "")
    if not delivery_id:
        delivery_id = f"{platform}-{hashlib.sha256(raw_body).hexdigest()}"

    if delivery_id in request.app.state.delivery_ids:
        logger.warning("Duplicate delivery ID detected: %s", delivery_id)
        raise HTTPException(status_code=409, detail="Duplicate delivery — already processed")
    request.app.state.delivery_ids.add(delivery_id)
    request.app.state._delivery_cache_ts[delivery_id] = time.time()
    delivery_cache: DeliveryCache | None = getattr(request.app.state, "_delivery_cache", None)
    if delivery_cache is not None:
        await delivery_cache.add(delivery_id)

    # ── Step 6: Check if trigger is reviewable ──────────────────────────
    from verdity.schemas import PullRequestRef, RepoRef, TriggerType, VerdityEvent

    # Reviewable trigger types that should be enqueued for review
    REVIEWABLE_TRIGGERS = {
        "pr.opened",
        "pr.synchronize",
        "pr.reopened",
        "pr.ready_for_review",
        "review_comment.created",
        "check_suite.rerequested",
        "push",
        "installation.created",
        "installation_repositories.added",
        "installation.deleted",
    }

    trigger_type_str = event_dict.get("trigger_type", "unknown")

    # Check if this is a valid reviewable trigger type
    try:
        trigger_type = TriggerType(trigger_type_str)
        is_reviewable = trigger_type_str in REVIEWABLE_TRIGGERS
    except ValueError:
        trigger_type = TriggerType.PR_OPENED
        is_reviewable = False

    # Record delivery ID for dedup (SG-07: record after the decision, but before ignoring)
    # This ensures ignored events still count toward dedup

    # ── Step 6a: Ignore non-reviewable events ────────────────────────
    if not is_reviewable:
        logger.info(
            "Ignoring non-reviewable event: platform=%s trigger=%s delivery=%s",
            platform,
            trigger_type_str,
            delivery_id,
        )
        await request.app.state.audit.append(
            event_type="webhook.ignored",
            entity_type="delivery",
            entity_id=delivery_id,
            payload={
                "platform": platform,
                "trigger_type": trigger_type_str,
                "action": event_dict.get("action", ""),
            },
        )
        return JSONResponse(
            status_code=202,
            content={
                "status": "ignored",
                "delivery_id": delivery_id,
                "platform": platform,
                "trigger_type": trigger_type_str,
                "message": f"Event type not reviewable: {trigger_type_str}",
            },
        )

    repo_dict = event_dict.get("repo", {})
    pr_dict = event_dict.get("pull_request", {})

    try:
        event = VerdityEvent(
            delivery_id=delivery_id,
            trigger_type=trigger_type,
            repo=RepoRef(
                owner=repo_dict.get("owner", ""),
                name=repo_dict.get("name", ""),
            ),
            pull_request=PullRequestRef(
                number=pr_dict.get("number", 0),
                head_sha=pr_dict.get("head_sha", ""),
                base_sha=pr_dict.get("base_sha", ""),
                title=pr_dict.get("title", ""),
                body=pr_dict.get("body", ""),
                author=pr_dict.get("author", ""),
                diff_url=pr_dict.get("diff_url", ""),
            ),
        )
    except Exception as exc:
        logger.error("Failed to create VerdityEvent from %s payload: %s", platform, exc)
        raise HTTPException(status_code=400, detail="Invalid event format") from exc

    # Sanitize paths
    pr = event.pull_request
    if pr:
        try:
            pr.head_sha = _sanitize_path(pr.head_sha)
            pr.base_sha = _sanitize_path(pr.base_sha)
        except ValueError as exc:
            logger.warning("Rejected suspicious PR ref: %s", exc)
            raise HTTPException(status_code=400, detail="Invalid PR reference") from exc

    envelope = QueueEnvelope(event=event)
    try:
        msg_id = await request.app.state.queue.publish(envelope)
    except (OSError, RuntimeError) as exc:
        logger.error("Queue publish failed: %s", exc)
        response = JSONResponse(
            status_code=503,
            content={"detail": "Queue unavailable"},
        )
        response.headers["Retry-After"] = "30"
        _add_security_headers(response)
        return response

    # ── Step 7: Audit log ─────────────────────────────────────────────
    await request.app.state.audit.append(
        event_type="webhook.ingested",
        entity_type="delivery",
        entity_id=delivery_id,
        payload={
            "platform": platform,
            "trigger_type": event.trigger_type.value,
            "repo": f"{event.repo.owner}/{event.repo.name}",
            "pr_number": event.pull_request.number if event.pull_request else None,
            "message_id": msg_id,
        },
    )

    logger.info(
        "Webhook accepted: platform=%s delivery=%s event=%s repo=%s/%s pr=%s",
        platform,
        delivery_id,
        event.trigger_type,
        event.repo.owner,
        event.repo.name,
        event.pull_request.number if event.pull_request else None,
    )

    return JSONResponse(
        status_code=202,
        content={
            "delivery_id": delivery_id,
            "status": "queued",
            "message_id": msg_id,
            "platform": platform,
        },
    )


@app.get("/verdity/health")
async def health():
    """Liveness/readiness probe."""
    return {"status": "ok", "service": "verdity-gateway"}


@app.get("/verdity/metrics/{repo_id:path}/dashboard")
async def get_metrics_dashboard(repo_id: str, days: int = 30, request: Request = ...):
    """Return chart-ready engineering metrics dashboard for a repo.

    Includes summary + daily breakdown for time-series visualization.
    """
    from verdity.config import get_settings

    settings = get_settings()
    token = settings.metrics_api_token.get_secret_value()
    if not token:
        return JSONResponse(status_code=503, content={"detail": "Metrics API not configured"})

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    provided_token = auth_header[len("Bearer ") :]
    if not hmac.compare_digest(token, provided_token):
        raise HTTPException(status_code=401, detail="Invalid token")

    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be between 1 and 365")

    metrics: MetricsStore | None = getattr(request.app.state, "metrics", None)
    if metrics is None:
        return JSONResponse(status_code=503, content={"detail": "Metrics store unavailable"})
    try:
        dashboard = await metrics.get_repo_dashboard(repo_id, days=days)
        return dashboard
    except (KeyError, ValueError, TypeError) as exc:
        logger.error("Failed to get dashboard for %s: %s", repo_id, exc)
        return JSONResponse(status_code=500, content={"detail": "Dashboard query failed"})


@app.get("/verdity/metrics/{repo_id:path}")
async def get_metrics(repo_id: str, days: int = 30, request: Request = ...):
    """Return aggregated engineering metrics for a repo.

    Query params:
      days: number of days to look back (default 30)
    """
    from verdity.config import get_settings

    settings = get_settings()
    token = settings.metrics_api_token.get_secret_value()
    if not token:
        return JSONResponse(status_code=503, content={"detail": "Metrics API not configured"})

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    provided_token = auth_header[len("Bearer ") :]
    if not hmac.compare_digest(token, provided_token):
        raise HTTPException(status_code=401, detail="Invalid token")

    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be between 1 and 365")

    metrics: MetricsStore | None = getattr(request.app.state, "metrics", None)
    if metrics is None:
        return JSONResponse(status_code=503, content={"detail": "Metrics store unavailable"})
    try:
        summary = await metrics.get_repo_summary(repo_id, days=days)
        return summary
    except (KeyError, ValueError, TypeError) as exc:
        logger.error("Failed to get metrics for %s: %s", repo_id, exc)
        return JSONResponse(status_code=500, content={"detail": "Metrics query failed"})
