# SG-13 — Orchestrator and worker lifecycle: bounded run cache, SLA loop wired, timeout gap

**Severity:** Medium · **Size:** S · **Depends on:** SG-12 · **Branch:** `fix/sg-13-orchestrator-lifecycle`

## Goal
A long-running worker uses bounded memory, runs the SLA escalation it already implements, and records a result for every specialist.

## Evidence
- `src/verdity/orchestrator.py:236`: `self._runs[review_run_id] = run` is never removed. Every run, with its findings, stays in memory for the life of the process.
- `src/verdity/worker.py` `_run_worker` builds `Worker(...)` without `approval_queue`, so `_sla_escalation_loop` never starts in production.
- `orchestrator.py`, `_gather_results`: a task cancelled by the outer `asyncio.wait` timeout hits `continue  # already handled by cancellation loop`, but nothing records a `SpecialistResponse` for it, so the specialist is missing from `run.specialist_results` and from the audit row. This is usually hidden because the inner `wait_for` uses the same timeout and returns first.
- `_run_adversarial_review` rebuilds each specialist's findings by `f.concern.value == resp.specialist`. A specialist that emits findings with another concern loses them. Check that this is intended.

## Plan (TDD)
1. **Red:** after N+1 runs with `max_cached_runs=N`, `get_run(oldest)` is `None`. Use a bounded `OrderedDict` and say in the docs that the audit store is the system of record.
2. **Red:** `_run_worker` builds and passes an `ApprovalQueueStore` (on the audit DB), and the SLA task starts.
3. **Red:** a specialist that ignores cancellation or overruns appears with `status="partial"` and a timeout error.
4. **Green:** make the changes above. For adversarial regrouping, keep each finding with its original specialist (track it by index or by `finding_id`) instead of matching on concern.

## Definition of done
- [ ] Run cache is bounded; SLA loop is wired; every specialist has a result.
- [ ] Adversarial regrouping keeps findings with the specialist that produced them (test).

## Commit
`fix(orchestrator): bound run cache, record timed-out specialists, wire SLA escalation`
