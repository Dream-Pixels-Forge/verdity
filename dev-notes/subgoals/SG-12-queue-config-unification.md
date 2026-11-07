# SG-12 — Gateway and worker must share the same queue (no silent in-memory fallback)

**Severity:** High (default deployment loses every event) · **Size:** S · **Depends on:** SG-11 · **Branch:** `fix/sg-12-queue-config`

## Goal
With default or documented configuration, events the gateway publishes are the ones the worker consumes. Misconfiguration fails loudly.

## Evidence
- Gateway: `EventQueue(db_path=settings.queue_sqlite_path)`, where `queue_sqlite_path` defaults to `":memory:"` (`config.py:55`). That's a separate process, so the in-memory queue is invisible to the worker and events are lost.
- Worker: `--queue-dsn` defaults to `sqlite:///verdity_queue.db`, and audit defaults to `verdity_audit.db` (gateway default: `audit_store.db`). The settings are ignored.
- `src/verdity/worker.py:235`: any DSN that isn't `sqlite:///` (for example `redis://…`, which the help text advertises) **silently becomes `:memory:`**.
- The worker passes `--audit-path` as the DB for audit, token economics *and* the semantic index (one file for three stores). That's probably intended, but document it.

## Plan (TDD)
1. **Red:** `parse_args([])` plus settings with `QUEUE_SQLITE_PATH=/tmp/x.db` means the worker uses `/tmp/x.db`. `--queue-dsn redis://h` raises `SystemExit` with "redis queue backend not implemented" (until a Redis backend exists). The gateway refuses to start with a `:memory:` queue unless `VERDITY_DEV_INMEMORY=1` (or log a loud warning — owner decides).
2. **Green:**
   - The worker's defaults come from `get_settings()` (queue and audit paths); the CLI flags override them.
   - Reject unsupported DSN schemes.
   - Change the `queue_sqlite_path` default to a file (for example `verdity_queue.db`) to match the worker.
3. Add a short "Running gateway + worker" section to the README with one `.env` both processes read.

## Definition of done
- [ ] An end-to-end test starts the gateway app and a worker on the same temp DB file; a signed webhook is consumed by the worker.
- [ ] There's no silent `:memory:` fallback anywhere (`grep -n ':memory:' src/verdity/worker.py src/verdity/gateway`).

## Commit
`fix(worker): share queue/audit config with gateway; reject unsupported queue DSNs`
