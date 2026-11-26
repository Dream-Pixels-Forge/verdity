# SG-21 — Deployable in one command: container images, compose, readiness

**Type:** Feature · **Size:** M · **Depends on:** SG-12 · **Branch:** `feat/sg-21-deploy`

## Why
There's no Dockerfile, compose file or deployment doc. Running Verdity today means hand-starting `uvicorn verdity.gateway.app:app` and `verdity-worker` with flags that (before SG-12) don't even point at the same queue. `/verdity/health` returns static OK. It doesn't check the queue or the audit DB, so an orchestrator can't tell that the gateway can't enqueue.

## Scope
1. **`Dockerfile`** (multi-stage, `python:3.12-slim`, non-root user, `pip install .` from a wheel built in the first stage). One image, two commands (`gateway` / `worker`).
2. **`deploy/compose.yml`:** gateway + worker + a shared volume for the SQLite DBs, with an optional `redis` profile (SG-22). Secrets come from `.env` (already templated by `.env.example`).
3. **Health split:**
   - `/verdity/health/live` returns process OK and stays cheap.
   - `/verdity/health/ready` checks that the queue is writable (`SELECT 1` + `count_by_state`), the audit store is connected and the delivery cache is loaded. It returns 503 with a reason if any check fails.
   - The worker writes a heartbeat (`worker_heartbeats` table or a file touched each loop) so `ready` can report "no live worker for > 60s".
4. **Graceful shutdown:** uvicorn `--timeout-graceful-shutdown`, and a worker `stop_grace_period` that's at least the `deep`-tier timeout. Use SG-16 leases for everything else.
5. **CI:** build the image on PRs and run the SG-14 e2e test *inside* the compose stack (`docker compose run --rm e2e`). Have `release.yml` push to GHCR on tags.

## Definition of done
- [ ] `docker compose -f deploy/compose.yml up` accepts a signed sample webhook (script `scripts/send_sample_webhook.py`) and the worker processes it.
- [ ] The readiness checks are covered by tests (queue down gives 503).
- [ ] README "Deploy" section has a 5-line quick start.

## Commit
`feat(deploy): Dockerfile, compose stack, live/ready health checks, worker heartbeat`
