# SG-13 — Orchestrator lifecycle: bounded run cache, real SLA, one timeout budget

**Severity:** Medium · **Size:** S · **Depends on:** SG-12 · **Branch:** `fix/sg-13-orchestrator-lifecycle`

## Goal
A long-running worker shouldn't leak memory or let a review overrun its policy timeout.

## Evidence (`src/verdity/orchestrator.py`)
- `self._runs: dict[UUID, ReviewRun]` grows forever, and every run holds its full `diff_files` and all findings. `list_runs()` sorts the whole dict on every call. In a worker this is an unbounded leak, and after a restart the history is gone anyway.
- The timeout is applied **twice**. `_run_specialist` wraps each `fn` in `wait_for(policy.timeout_seconds)`, and `_gather_results` *also* wraps the gather in `wait_for(policy.timeout_seconds)`. The adversarial review and metrics then run with **no** budget at all, so the wall-clock time per run can exceed `timeout_seconds`.
- The run status is only `PARTIAL` when a specialist reports `"partial"`. A run where **every** specialist `failed` or timed out is reported as `COMPLETED`.
- `start_nightly_recalibration` creates a task the orchestrator never cancels on shutdown.

## Plan (TDD)
1. **Red**
   - After `max_cached_runs + 10` runs, `len(_runs) == max_cached_runs` and the oldest runs are evicted.
   - A slow adversarial reviewer can't push the total run time above `timeout_seconds + ε`.
   - All specialists failing gives `RunStatus.FAILED`. A mix of failed and ok gives `PARTIAL`.
   - `await orch.aclose()` cancels the recalibration task.
2. **Green**
   - Make `_runs` an `OrderedDict` with LRU eviction (`max_cached_runs=200`). Drop `diff_files` from cached runs once the run completes.
   - Use one deadline per run (`loop.time() + timeout`). Each phase gets `remaining()`, the per-specialist `wait_for` uses the remaining time, and the outer `wait_for` is removed.
   - Status rule: all failed → FAILED; any failed, timed out or partial → PARTIAL; otherwise COMPLETED.
   - Add `aclose()`, and have the worker call it on shutdown.
3. Optional: persist `ReviewRun` summaries to the audit or metrics DB so `get_run` survives restarts. That feeds SG-19.

## Definition of done
- [ ] The four Red tests pass.
- [ ] There's no unbounded per-run state in the worker process.

## Commit
`fix(orchestrator): bounded run cache, single deadline per run, correct FAILED status`
