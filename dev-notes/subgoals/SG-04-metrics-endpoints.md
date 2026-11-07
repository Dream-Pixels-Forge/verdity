# SG-04 — Metrics endpoints: fix the shadowed dashboard route and add authentication

**Severity:** High (confirmed routing bug + open data exposure) · **Size:** S · **Depends on:** SG-01 · **Branch:** `fix/sg-04-metrics-routes`

## Goal
Make `/verdity/metrics/{repo}/dashboard` reachable, and stop anonymous callers from reading per-repo engineering metrics.

## Evidence (`src/verdity/gateway/app.py`)
- `@app.get("/verdity/metrics/{repo_id:path}")` (around line 903) is registered before `/verdity/metrics/{repo_id:path}/dashboard` (around line 921). The `:path` converter is greedy, so `GET /verdity/metrics/acme/web/dashboard` goes to `get_metrics` with `repo_id="acme/web/dashboard"`. Confirmed by probe.
- Neither endpoint checks any credentials.
- Both read `app.state` from the module-level `app` instead of `request.app`, which makes them harder to test with alternate apps.

## Plan (TDD)
1. **Red:** an ASGI test where `GET /verdity/metrics/acme/web/dashboard` must return the dashboard shape (`summary`, `daily_reviews`, `daily_costs`).
2. **Red:** tests that a request without a token gets 401, a wrong token gets 401, the right token gets 200, and no token configured means the endpoints are disabled (404 or 503 — decide and document).
3. **Green:**
   - Register the dashboard route first, or use explicit `{owner}/{repo}` segments: `/verdity/metrics/{owner}/{repo}` and `/verdity/metrics/{owner}/{repo}/dashboard`. Explicit segments are recommended because repo ids are always `owner/name`.
   - Add a `metrics_api_token: SecretStr` setting and check `Authorization: Bearer …` with `hmac.compare_digest`.
   - Use `request.app.state`.
   - Validate `days` (`1 ≤ days ≤ 365`).
4. Document the new setting in `.env.example` and the README.

## Definition of done
- [ ] The dashboard route is reachable (test).
- [ ] Unauthenticated access is rejected (tests).
- [ ] The setting is documented; coverage stays at 100%.

## Commit
`fix(gateway): reachable metrics dashboard route; require bearer token for metrics`
