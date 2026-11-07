# SG-11 — Worker: backoff must not dead-letter messages; implement per-repo ordering

**Severity:** High (one failing PR can dead-letter a whole repo's queue) · **Size:** M · **Depends on:** SG-01 · **Branch:** `fix/sg-11-worker-backoff`

## Goal
Messages are only dead-lettered after real processing failures, and messages for the same repo are processed one at a time, as the docstring says.

## Evidence (`src/verdity/worker.py`)
- `_drain_one`, line 134: while a repo is backing off, any other message for that repo is `nack`ed with `"backoff"`. `EventQueue.nack` increments `retry_count` and moves the message to `dead` at 3 (`event_queue.py:168–193`). Because consume picks up the pending message again within about 1 s, **a healthy message is dead-lettered in about 3 s without ever being attempted.**
- Its own retries also get no delay: after a failure the message is `pending` again right away, so the backoff only "works" by burning retries.
- The class docstring promises "per-repo ordering (one message at a time per repo)", but up to `max_concurrent` (4) messages for the same repo run at once, so a `synchronize` can race the `opened` review it supersedes.

## Plan (TDD)
1. **Red:** with a fake orchestrator that fails repo A's first message, a second repo A message must eventually be processed and not end in `dead`. Repo B messages keep flowing during A's backoff.
2. **Red:** two messages for the same repo never overlap (the fake orchestrator records start and end times).
3. **Green**, recommended approach:
   - Add `EventQueue.defer(message_id, until)` (a `not_before` column, plus a migration) that requeues **without** incrementing `retry_count`. `consume` skips rows where `not_before > now`.
   - On a processing failure, `nack` (counts a retry) with `not_before = now + backoff`.
   - For per-repo ordering, keep an in-flight set of repo ids and `defer` messages whose repo is busy. Alternatively, `consume(exclude_repos=...)`.
4. Add a schema migration for the existing `queue_messages` table (`ALTER TABLE … ADD COLUMN not_before`).

## Definition of done
- [ ] There's no path where waiting or backoff increments `retry_count`.
- [ ] The per-repo serial test passes.
- [ ] The migration is tested on an existing DB file.

## Commit
`fix(worker): defer without consuming retries during backoff; serialize per repo`
