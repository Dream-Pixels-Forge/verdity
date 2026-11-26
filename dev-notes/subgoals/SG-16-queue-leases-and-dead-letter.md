# SG-16 — Queue leases: recover stuck messages, atomic claim, dead-letter replay

**Severity:** High (silent loss) · **Size:** M · **Depends on:** SG-11 · **Branch:** `fix/sg-16-queue-leases`

## Goal
No message should stay stuck in `processing` forever, and an operator should be able to see and replay dead messages.

## Evidence (`src/verdity/event_queue.py`, checked 2026-11-26)
- `consume()` moves a row to `processing` and nothing ever moves it back. If a worker crashes, is OOM-killed or redeploys mid-review, the message is **stuck permanently**. It's neither retried nor dead-lettered. There's no lease, heartbeat or visibility timeout.
- The claim isn't atomic: `SELECT … LIMIT 1` then `UPDATE … WHERE message_id = ?` are two statements. Two workers (or two connections) can both claim the same row. `acknowledge` has a `state='processing'` guard but `consume` doesn't.
- Ordering uses `created_at` with **second** resolution (`strftime('%Y-%m-%dT%H:%M:%SZ')`). Messages from the same second come back in arbitrary order. `id INTEGER AUTOINCREMENT` is already monotonic and should be the tie-breaker.
- `nack` clears `error_msg` on a retry, so the cause of earlier failures is lost.
- There's no API to list or replay `dead` messages. `count_by_state` only shows counts.

## Plan (TDD)
1. **Red**
   - Consume, then don't ack. After `lease_seconds` (injected clock), `reclaim_expired()` puts the message back to `pending` with `retry_count + 1`. After max retries it becomes `dead`.
   - Two concurrent `consume()` calls on one pending row: exactly one returns it.
   - Three messages published in the same second come back in publish order.
   - `list_dead()` returns the dead rows with their last error, and `replay(msg_id)` makes a message pending again with `retry_count = 0`.
2. **Green**
   - Add the columns `lease_expires_at`, `attempts`, `last_error` (via an idempotent `ALTER TABLE`; see the migration note below).
   - Make the claim a single statement: `UPDATE … SET state='processing', lease_expires_at=? WHERE id = (SELECT id … ORDER BY id LIMIT 1) AND state='pending' RETURNING *` (SQLite ≥ 3.35, which ships with Python 3.11+).
   - Add `extend_lease(msg_id)` so the orchestrator can heartbeat during long `deep`-tier runs.
   - Have the worker call `reclaim_expired()` at startup and every N seconds.
   - Add a `verdity-queue` CLI: `stats | dead | replay <id> | purge --acked-older-than 7d`.
3. **Migration note:** add a tiny `schema_version` table and run additive migrations in `connect()`. Reuse the pattern for the audit, approval and metrics stores later.

## Definition of done
- [ ] Crash-recovery, atomic-claim, ordering and replay tests pass.
- [ ] The `verdity-queue` script is registered in `pyproject.toml` and documented in the README.

## Commit
`fix(queue): lease-based processing with reclaim, atomic claim, dead-letter replay CLI`
