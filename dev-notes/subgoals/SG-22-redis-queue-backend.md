# SG-22 — Redis Streams queue backend (make `queue_backend=redis` real)

**Type:** Feature · **Size:** M · **Depends on:** SG-12, SG-16 · **Branch:** `feat/sg-22-redis-queue`

## Why
`Settings.queue_backend: Literal["redis","sqlite"]` and `queue_redis_url` are advertised, and the architecture doc says "partitioned by repo". Only SQLite exists. SQLite with one writer is fine for a single box, but it can't support several worker replicas on different hosts.

## Scope
- Define a `QueueBackend` protocol extracted from `EventQueue`: `publish, consume(exclude_repos), acknowledge, nack, release, extend_lease, reclaim_expired, list_dead, replay, count_by_state`. Rename the current class `SqliteQueue`.
- Add a `RedisStreamQueue`:
  - one stream `verdity:events`, consumer group `workers`, `XADD` with `delivery_id` as a field;
  - idempotent publish via `SET verdity:msg:{delivery_id} NX EX 86400`;
  - lease = pending entry list + `XAUTOCLAIM` with min-idle = lease time (built-in reclaim);
  - per-repo serialisation via `SET verdity:repo-lock:{repo} {worker} NX PX lease` (release on ack);
  - dead letters go to `verdity:dead` (a stream) with the last error.
- Have `build_queue(settings)` (SG-12) pick the backend. `redis` stays an optional extra (`pip install verdity[redis]`, already used by the rate limiter).

## Plan (TDD)
- Run one shared **contract test suite** (`tests/queue_contract.py`) against both backends with `pytest.mark.parametrize`. Redis runs against `fakeredis` in unit CI, plus a real `redis:7` service container in one CI job.
- Every SG-11 and SG-16 behaviour (defer without retry burn, per-repo serialisation, crash reclaim, replay) must pass on both.

## Definition of done
- [ ] The contract suite is green on both backends.
- [ ] The SG-21 compose `redis` profile works with 2 worker replicas and never processes the same repo concurrently (assert via audit timestamps).

## Commit
`feat(queue): Redis Streams backend behind QueueBackend protocol with shared contract tests`
