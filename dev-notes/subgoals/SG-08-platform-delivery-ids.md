# SG-08 — Correct per-delivery IDs for Bitbucket and a deterministic fallback

**Severity:** High (Bitbucket: everything after the first event is rejected) · **Size:** S · **Depends on:** SG-07 · **Branch:** `fix/sg-08-delivery-ids`

## Goal
Each platform delivery gets a stable, unique dedup key.

## Evidence
- `src/verdity/platforms/bitbucket.py:102` uses `X-Hook-UUID`. Bitbucket documents that as the UUID of the **webhook**, which is the same for every delivery. The per-request header is `X-Request-UUID`. Result: after the first event, every Bitbucket event gets 409 for 24 hours.
- `src/verdity/gateway/app.py`, `handle_platform_webhook`: when there's no delivery ID, the fallback is `f"{platform}-{hash(raw_body.hex()[:64])}"`.
  - Python's `hash()` of a `str` is randomised per process (`PYTHONHASHSEED`), so it doesn't hold across restarts or workers.
  - `hex()[:64]` is only the first 32 bytes of the body, so different events that start the same way collide and get false 409s.
- GitLab uses `X-Gitlab-Event-UUID`. That's correct, but check it's present on all supported GitLab versions.

## Plan (TDD)
1. **Red:** two Bitbucket deliveries with the same `X-Hook-UUID` but different `X-Request-UUID` must both get 202. A fallback key must be identical across two processes (compute it in a subprocess, or assert it's a SHA-256 of the full body).
2. **Green:**
   - Bitbucket: `delivery_id = headers.get("x-request-uuid") or ""`.
   - Fallback: `f"{platform}-{hashlib.sha256(raw_body).hexdigest()}"`.
   - Update the log lines that print `x-hook-uuid`.
3. Update the platform fixture payloads and headers in the tests to realistic ones.

## Definition of done
- [ ] Each platform has a test that uses its real header names.
- [ ] No `hash()` is used for persistence anywhere (`grep -n "hash(" src/`).

## Commit
`fix(platforms): use X-Request-UUID for Bitbucket; deterministic sha256 fallback dedup key`
