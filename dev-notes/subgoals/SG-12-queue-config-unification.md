# SG-12 — Gateway and worker must use the same queue

**Severity:** High · **Size:** S · **Depends on:** SG-11, SG-16 · **Branch:** `fix/sg-12-queue-config`

## Goal
Webhooks the gateway accepts must reach the worker. Today, in the default configuration, they don't.

## Evidence
- Gateway (`gateway/app.py` lifespan): `EventQueue(db_path=settings.queue_sqlite_path)`, and `queue_sqlite_path` defaults to **`:memory:`** (`config.py:55`). Every accepted webhook lives in RAM inside the gateway process and disappears on restart.
- Worker (`worker.py:_run_worker`): reads its own `--queue-dsn` (default `sqlite:///verdity_queue.db`) and **ignores `Settings`**.
- A worker given `--queue-dsn redis://…` silently falls back to `":memory:"` (`else ":memory:"`). That's a configured production queue replaced by an empty one, with no error.
- `queue_backend: Literal["redis","sqlite"]` and `queue_redis_url` exist in `Settings` but nothing reads them. The worker also passes `--audit-path` for audit, token economics *and* the semantic index (one DB file shared by three stores), while the gateway uses `settings.audit_sqlite_path`.

## Plan (TDD)
1. **Red**
   - A test builds the gateway app and the worker from the **same** env (`QUEUE_SQLITE_PATH=tmp/q.db`), posts a webhook, and asserts the worker's `consume()` returns it.
   - A worker started with a `redis://` DSN while the Redis backend is missing raises a clear `ConfigError` (exit code 2), not `:memory:`.
   - Starting the gateway with `queue_sqlite_path=":memory:"` logs a **warning**. When `VERDITY_ENV=production` it fails to start.
2. **Green**
   - Add `verdity.queue_factory.build_queue(settings) -> EventQueue` as the single source of truth, used by both processes.
   - Worker CLI flags become *overrides* of `Settings`, not a separate config system. Remove the DSN string parsing.
   - Change the default `queue_sqlite_path` to `verdity_queue.db` (a file). Keep `:memory:` for tests via the fixture.
   - Give the semantic index and token economics their own settings paths (`semantic_index_sqlite_path`, `token_economics_sqlite_path`).
3. Update `.env.example` and the README "Configuration" section.

## Definition of done
- [ ] The end-to-end gateway→worker hand-off test passes.
- [ ] No silent `:memory:` fallback remains (`grep -n '":memory:"' src/verdity/worker.py` finds nothing).
- [ ] Both processes build their stores from `Settings`.

## Commit
`fix(queue): single queue factory shared by gateway and worker; no silent in-memory fallback`
