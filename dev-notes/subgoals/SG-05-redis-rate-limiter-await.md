# SG-05 — Await the Redis rate limiter in the middleware

**Severity:** High (confirmed: enabling it returns 500 on every GitHub webhook) · **Size:** XS · **Depends on:** SG-01 · **Branch:** `fix/sg-05-redis-limiter-await`

## Goal
`REDIS_RATE_LIMITER_ENABLED=true` must rate-limit requests, not crash them.

## Evidence (`src/verdity/gateway/app.py`)
- `rate_limit_middleware` (around line 515) calls `limiter.is_allowed(request)` synchronously. `RedisRateLimiter.is_allowed` is `async`, so unpacking the coroutine raises `TypeError: cannot unpack non-iterable coroutine object`. Confirmed by probe.
- `tests/test_redis_rate_limiter.py` only calls the limiter directly and never goes through the app, which is why this was missed.
- The type hint `limiter: _RateLimiter | None` hides the mismatch.
- A related issue: after a Redis error, `_connected=False` is set and nothing ever reconnects, despite the comment "Try to reconnect on next request".

## Plan (TDD)
1. **Red:** an ASGI test that sets `app.state._rate_limiter` to a `RedisRateLimiter` with a mocked `redis.asyncio` client and POSTs a signed webhook. It must get 202, not 500. Add a second test where the limiter denies the request and the response is 429 with `Retry-After`.
2. **Green:** give both limiters the same async interface (`async def is_allowed`); the in-memory one can simply be `async`. Always `await` it in the middleware. Define a `Protocol` type for the limiter.
3. **Green:** add a reconnect attempt with a cooldown (for example once every 30 s), or remove the misleading comment.
4. While here, make `close()` use `aclose()` if the installed redis-py deprecates `close()` (warnings are errors in pytest config).

## Definition of done
- [ ] The middleware path is tested with both limiter types.
- [ ] There's no sync/async mismatch, and the type hint is a Protocol.
- [ ] Reconnect behaviour is implemented or the comment is corrected.

## Commit
`fix(gateway): await async Redis rate limiter in middleware`
