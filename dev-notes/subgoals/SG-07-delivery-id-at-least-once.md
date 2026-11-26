# SG-07 — Record the delivery ID only after the enqueue succeeds

**Severity:** High (data loss) · **Size:** S · **Depends on:** SG-06 · **Branch:** `fix/sg-07-delivery-id-after-enqueue`

## Goal
A webhook that fails to enqueue must be retryable. Today the gateway rejects the provider's retry as a duplicate, so the event is lost.

## Evidence (`src/verdity/gateway/app.py`, checked 2026-11-26)
- GitHub handler: the delivery ID goes into `state.delivery_ids`, `_delivery_cache_ts` and the persistent `DeliveryCache` at lines ~679–684 (**Step 3**). `queue.publish()` runs later at ~780 (**Step 8**).
- If `publish()` raises, the handler returns 503 and GitHub redelivers with the **same** `X-GitHub-Delivery`. The retry hits the cache and gets **409**, so the event is never processed.
- The platform handler (`handle_platform_webhook`) has the same order: it adds the ID at ~921–928 and enqueues afterwards.
- Non-reviewable events (SG-09) also go into the cache before the early 202. That's harmless, but the order should be stated deliberately.
- Today the check and the add have no `await` between them, so there's no race. Moving the add to *after* `await publish()` **creates** one: two concurrent requests with the same ID would both pass the check. The fix has to close it.

## Plan (TDD)
1. **Red**
   - Make `queue.publish` raise once: the first request gets 503. The second request with the same delivery ID gets **202** and the message is in the queue. Cover both GitHub and the platform route.
   - Two concurrent requests with the same ID: exactly one is enqueued and the other gets 409.
2. **Green**
   - Split the step in two. The **check** stays early and rejects IDs already confirmed. The **record** step (memory, timestamp and `DeliveryCache.add`) moves to *after* a successful `publish()`.
   - Close the race with a per-ID reservation (`state.delivery_inflight: set[str]` checked and added before any `await`), released in `finally`. `EventQueue.publish` is already `INSERT OR IGNORE` on `message_id`, which gives a second line of defence.
   - Pull this into one helper, `_dedup_and_enqueue(request, delivery_id, envelope)`, used by both handlers.
3. Document the guarantee (at-least-once, gateway-side idempotent) in `dev-notes/03-API-Webhook-Specification.md`.

## Definition of done
- [ ] A retry after a failed enqueue is accepted (tested on both routes).
- [ ] The concurrent duplicate test passes.
- [ ] The dedup code lives in one helper and the two handlers don't duplicate it.

## Commit
`fix(gateway): record delivery ID only after successful enqueue (at-least-once)`
