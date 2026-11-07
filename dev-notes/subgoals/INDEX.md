# Verdity fix plan — subgoals from the 2026-11-06 review

Work through these **one at a time, in order**. Each subgoal has its own branch, its own failing-test-first plan, and its own Definition of Done. Don't start the next one until the current one is merged.

**Baseline (2026-11-06):** 1305 tests pass · 100% coverage · ruff clean.

## Rules for every subgoal
1. Branch from an up-to-date `master` using the name given in the file (`fix/…`, `feat/…`, `chore/…`).
2. TDD: write the failing test first (**Red**), make it pass (**Green**), then refactor.
3. Before opening the PR, all of these must pass:
   ```bash
   ruff check src tests && ruff format --check src tests
   python -m pytest        # includes --cov-fail-under=100
   ```
4. Use the conventional commit message given in the file. Squash-merge, then delete the branch.
5. Tick the subgoal off below and note the PR link.

## Order

| # | Subgoal | Severity | Size | Depends on | Status |
|---|---------|----------|------|------------|--------|
| 01 | [Baseline: dev env, hygiene, packaging](SG-01-baseline-and-hygiene.md) | Low (enabler) | S | — | ☑ |
| 02 | [MCP `llm_enrich_findings` crash](SG-02-mcp-llm-enrich-crash.md) | High · confirmed | S | 01 | ☑ #65 |
| 03 | [`verdity-enrich` CLI stdin / `--output-file`](SG-03-cli-enrich.md) | Medium | S | 02 | ☑ #66 |
| 04 | [Metrics routes: shadowed dashboard + auth](SG-04-metrics-endpoints.md) | High · confirmed | S | 01 | ☑ #67 |
| 05 | [Await Redis rate limiter in middleware](SG-05-redis-rate-limiter-await.md) | High · confirmed | XS | 01 | ☑ #68 |
| 06 | [Trusted-proxy client IP (XFF spoofing)](SG-06-trusted-proxy-client-ip.md) | High · security | M | 05 | ☑ |
| 07 | [Record delivery ID after enqueue](SG-07-delivery-id-at-least-once.md) | High · data loss | S | 06 | ☐ |
| 08 | [Bitbucket `X-Request-UUID` + deterministic fallback](SG-08-platform-delivery-ids.md) | High | S | 07 | ☐ |
| 09 | [Ignore non-reviewable events](SG-09-trigger-type-mapping.md) | High | S | 08 | ☑ #71 |
| 10 | [GitLab MR field mapping (check first)](SG-10-gitlab-payload-fields.md) | High if confirmed | S | 09 | ☐ |
| 11 | [Worker backoff + per-repo ordering](SG-11-worker-backoff-and-ordering.md) | High | M | 01 | ☐ |
| 12 | [Gateway and worker share the queue](SG-12-queue-config-unification.md) | High | S | 11 | ☐ |
| 13 | [Orchestrator lifecycle (run cache, SLA, timeouts)](SG-13-orchestrator-lifecycle.md) | Medium | S | 12 | ☐ |
| 14 | [Connect the webhook pipeline end to end](SG-14-wire-production-pipeline.md) | **Critical** | L | 07–13 | ☐ |
| 15 | [Restore 100% coverage gate + release 0.5.1](SG-15-coverage-gate-and-release.md) | Medium | S | all | ☑ |

## Why this order
- **01** gives a reproducible test command for everything after it.
- **02–05** are small fixes for bugs that were confirmed by running them. They're quick, low-risk, and bring coverage back up.
- **06–10** harden the gateway from the outside in: client identity, then dedup ordering, then dedup keys, then which events are accepted, then payload parsing. Each one builds on the previous handler shape.
- **11–13** make the worker and queue trustworthy before real work runs through them.
- **14** is the large feature (an actual end-to-end review). It goes last because it relies on all the delivery guarantees above.
- **15** closes the coverage gate and ships the release with the three-way version check.
