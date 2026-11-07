# SG-07 — Record the delivery ID only after a successful enqueue (keep at-least-once delivery)

**Severity:** High (data loss on retry) · **Size:** S · **Depends on:** SG-06 · **Branch:** `fix/sg-07-dedupe-after-enqueue`

## Goal
A webhook that fails with 400 or 503 must be accepted when GitHub redelivers it, and a successful one must still be rejected as a duplicate.

## Evidence (`src/verdity/gateway/app.py`)
- `handle_github_webhook` adds `x_github_delivery` to `delivery_ids` and the SQLite `DeliveryCache` at "Step 3", **before** JSON parsing, normalization, sanitization and `queue.publish`. If any later step fails (400, or 503 "Queue unavailable"), GitHub's redelivery uses the same delivery GUID and gets **409**, so the event is lost for good.
- `handle_platform_webhook` has the same ordering.
- `_cleanup_delivery_cache` only evicts IDs in `_delivery_cache_ts`. IDs loaded from SQLite at startup are never removed from memory, and the SQLite table is only pruned at shutdown.
- `EventQueue.publish` already uses `INSERT OR IGNORE` on `message_id = delivery_id` (`event_queue.py:87`). That's a safe backstop for concurrent duplicates.

## Plan (TDD)
1. **Red:** tests where the queue raises `OSError` on the first POST (expect 503) and the same delivery is retried (expect 202, published once); where a malformed body returns 400 and a retry with a valid body is accepted; and where a delivery already accepted still gets 409.
2. **Red:** test that IDs loaded at startup are evicted from memory after the TTL.
3. **Green:**
   - Order the handler as: verify, then a read-only duplicate check, then parse, normalize, sanitize, publish, and only then record the delivery ID (memory and SQLite).
   - Pull the shared steps of both handlers into one internal function so the ordering lives in one place.
   - Record a timestamp for loaded IDs (`load_recent` returns `(id, created_at)`) and run `evict_expired()` from the periodic cleanup, not just at shutdown.
4. Note the remaining race (two identical deliveries in flight at once) and why `INSERT OR IGNORE` makes it safe.

## Definition of done
- [ ] Retry-after-failure tests pass for both handlers.
- [ ] Memory and SQLite caches are both bounded by the TTL.
- [ ] The two handlers share one pipeline function; coverage stays at 100%.

## Commit
`fix(gateway): mark delivery as seen only after successful enqueue`
