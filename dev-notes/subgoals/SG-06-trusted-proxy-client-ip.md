# SG-06 — Stop trusting `X-Forwarded-For` blindly (IP allowlist and rate-limit bypass)

**Severity:** High (security) · **Size:** M · **Depends on:** SG-05 · **Branch:** `fix/sg-06-trusted-proxy`

## Goal
Work out the client IP safely, so the IP allowlist and the rate limiter can't be bypassed by setting a header.

## Evidence (`src/verdity/gateway/app.py`)
- `_RateLimiter._client_ip`, `RedisRateLimiter._client_ip` and `ip_allowlist_middleware` all take `X-Forwarded-For.split(",")[0]` unconditionally.
  - **Allowlist bypass:** send `X-Forwarded-For: 140.82.112.1` from any host. HMAC still protects the webhook, but the allowlist control is useless.
  - **Rate-limit bypass:** change the header on each request to get a fresh bucket each time. Memory also grows without limit (one bucket per fake IP, never removed).
- The allowlist is **on by default** (`DEFAULT_GITHUB_WEBHOOK_IPS`). Behind a proxy that doesn't set XFF, `request.client.host` is the proxy's IP, so every webhook gets 403.
- The default list includes `40.126.0.0/16` and two `/29`s; check them against `https://api.github.com/meta` (`hooks`). The IPv6 ranges are missing.
- The client-IP logic is copied in 3 places.

## Plan (TDD)
1. **Red:** tests showing that a spoofed XFF from an untrusted peer is ignored by both the allowlist and the limiter; that with `TRUSTED_PROXIES=10.0.0.0/8` and the peer in range, the right-most untrusted hop in XFF is used; and that the in-memory limiter drops empty buckets.
2. **Green:**
   - Add a `trusted_proxies: str` setting (CIDRs, default empty, meaning XFF is never used).
   - Add one helper, `client_ip(request, trusted)`: if the peer isn't trusted, return the peer. Otherwise walk XFF from the right and return the first address that isn't trusted.
   - Use the helper in all 3 places.
   - Make the in-memory limiter prune empty buckets, and use `collections.deque` instead of `list.pop(0)`.
3. Decide (owner) whether the allowlist stays on by default. Recommendation: off unless configured, with HMAC as the real control. Document it.
4. Refresh the default GitHub ranges from `/meta`, including IPv6, and add a comment on how to update them.

## Definition of done
- [x] Spoofing tests pass; there's one client-IP helper.
- [x] `TRUSTED_PROXIES` is documented in `.env.example` and the security docs.
- [x] The default ranges have been checked against GitHub `/meta`.

## Commit
`fix(security): derive client IP via trusted-proxy list; stop trusting X-Forwarded-For`
