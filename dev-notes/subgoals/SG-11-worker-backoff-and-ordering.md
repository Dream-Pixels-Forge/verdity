# SG-11 — Worker backoff that doesn't burn retries, and real per-repo ordering

**Severity:** High · **Size:** M · **Depends on:** SG-01 · **Branch:** `fix/sg-11-worker-backoff-ordering`

## Goal
Make the worker do what its docstring says: exponential backoff per repo, and **one message at a time per repo**.

## Evidence (`src/verdity/worker.py`, `src/verdity/event_queue.py`)
1. **Backoff kills healthy messages.** When a repo is in backoff, `_drain_one` calls `queue.nack(msg_id, error_msg="backoff")`. `nack` always does `retry_count + 1` and moves the message to `dead` at 3. So a repo in backoff sends its *next, unrelated* messages to the dead-letter state after about 3 polls, and none of them ever ran.
2. **There's no per-repo ordering.** `consume()` takes the oldest pending message from *any* repo. With `max_concurrent=4`, two `pr.synchronize` events for the same PR can run at the same time and finish out of order, so a stale review can be posted last.
3. **The first backoff is off by one step.** On the first failure, `current = backoff_initial` and `new = current * factor`, so the first wait is 2s instead of 1s. The test name says initial = 1.0.
4. **Busy loop while in backoff.** Nack, then `sleep(min(remaining, 1))`, then consume again picks the *same* message (it's still the oldest pending one), with no progress on other repos.
5. `_backoffs` / `_backoff_expiry_times` are never pruned for repos that stop failing after they expire.

## Plan (TDD)
1. **Red**
   - A repo in backoff: its pending messages keep `retry_count == 0` and are never `dead`.
   - Two messages for the same repo with `max_concurrent=4` never overlap (track entry and exit with an `asyncio.Event`).
   - A message for repo B is processed while repo A is in backoff.
   - The first failure waits `backoff_initial`.
2. **Green**
   - Add `EventQueue.release(msg_id)`: set it back to `pending` **without** touching `retry_count` (the "defer" path). Reserve `nack` for real failures.
   - Add `consume(exclude_repos: set[str])` (SQL `repo_id NOT IN (...)`). The worker passes `in_flight_repos | repos_in_backoff`. That gives per-repo serialisation and lets other repos make progress.
   - Track `self._in_flight_repos: set[str]`, added before dispatch and removed in `finally`.
   - Backoff: `next = initial * factor ** (attempts - 1)`, capped. Clear it on success and prune expired entries.
3. Have the orchestrator, or SG-14's poster, check `head_sha` before posting so a superseded run can't overwrite a newer one. Add a note here and do the work in SG-14.

## Definition of done
- [ ] All four Red tests pass, and the existing worker tests still pass.
- [ ] The `Worker` docstring matches the behaviour.

## Commit
`fix(worker): defer without burning retries; serialize per repo; correct backoff curve`
