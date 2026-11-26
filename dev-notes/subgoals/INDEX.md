# Verdity subgoals

Work through these **one at a time, in order**. Each subgoal has its own branch, its own failing-test-first plan, and its own Definition of Done. Don't start the next one until the current one is merged.

**Baseline (2026-11-26):** 1358 tests pass · 100% coverage · ruff clean (`pip install -e ".[dev]" && python -m pytest`).

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

---

## Track A — Fix plan from the 2026-11-06 review

Files for the subgoals already done (01–05, 08–10, 15) were never committed. Their commits are linked instead.

| # | Subgoal | Severity | Size | Depends on | Status |
|---|---------|----------|------|------------|--------|
| 01 | Baseline: dev env, hygiene, packaging | Low (enabler) | S | — | ☑ |
| 02 | MCP `llm_enrich_findings` crash | High · confirmed | S | 01 | ☑ #65 |
| 03 | `verdity-enrich` CLI stdin / `--output-file` | Medium | S | 02 | ☑ #66 |
| 04 | Metrics routes: shadowed dashboard + auth | High · confirmed | S | 01 | ☑ #67 |
| 05 | Await Redis rate limiter in middleware | High · confirmed | XS | 01 | ☑ #68 |
| 06 | [Trusted-proxy client IP (XFF spoofing)](SG-06-trusted-proxy-client-ip.md) | High · security | M | 05 | ☑ f8f185c |
| 07 | [Record delivery ID after enqueue](SG-07-delivery-id-at-least-once.md) | High · data loss | S | 06 | ☐ |
| 08 | Bitbucket `X-Request-UUID` + deterministic fallback | High | S | 07 | ☑ 4f16974 |
| 09 | Ignore non-reviewable events | High | S | 08 | ☑ #71 |
| 10 | GitLab MR field mapping | High | S | 09 | ☑ #72 |
| 11 | [Worker backoff + per-repo ordering](SG-11-worker-backoff-and-ordering.md) | High | M | 01 | ☐ |
| 12 | [Gateway and worker share the queue](SG-12-queue-config-unification.md) | High | S | 11, 16 | ☐ |
| 13 | [Orchestrator lifecycle (run cache, deadline, status)](SG-13-orchestrator-lifecycle.md) | Medium | S | 12 | ☐ |
| 14 | [Connect the webhook pipeline end to end](SG-14-wire-production-pipeline.md) | **Critical** | L | 07, 11–13, 16, 17 | ☐ |
| 15 | Restore 100% coverage gate + release 0.5.1 | Medium | S | all | ☑ #64 |

## Track B — 2026-11-26 review: new fixes

| # | Subgoal | Severity | Size | Depends on | Status |
|---|---------|----------|------|------------|--------|
| 16 | [Queue leases, atomic claim, dead-letter replay](SG-16-queue-leases-and-dead-letter.md) | High · silent loss | M | 11 | ☐ |
| 17 | [Gate every post through the router (constraint #7)](SG-17-confidence-gated-posting.md) | **Critical** | M | — | ☐ |
| 18 | [Repo hygiene](SG-18-repo-hygiene.md) | Low | XS | — | ◐ (coverage-gate.yml decision open) |

## Track C — New features

| # | Subgoal | Value | Size | Depends on | Status |
|---|---------|-------|------|------------|--------|
| 19 | [Approval inbox + human feedback → calibration](SG-19-approval-inbox-and-feedback-loop.md) | Closes the trust loop | M | 14, 17 | ☐ |
| 20 | [Inline suggestions + incremental re-review](SG-20-inline-and-incremental-review.md) | Developer adoption | L | 14, 17 | ☐ |
| 21 | [Docker/compose deploy + live/ready health](SG-21-deployment-packaging.md) | Operability | M | 12 | ☐ |
| 22 | [Redis Streams queue backend](SG-22-redis-queue-backend.md) | Horizontal scale | M | 12, 16 | ☐ |
| 23 | [Real embeddings + vector store](SG-23-real-embeddings-and-vector-store.md) | Cross-file findings | L | 14 | ☐ |
| 24 | [Evaluation harness (precision/recall/ECE in CI)](SG-24-evaluation-harness.md) | Proves calibration | M | 17 | ☐ |

## Recommended order
**18 → 17 → 07 → 11 → 16 → 12 → 13 → 14 → 24 → 19 → 20 → 21 → 22 → 23**

- **18** first: it's trivial and makes the tree clean for everything after it.
- **17** next: today `verdity-review --post-check` posts *every* finding, so Verdity's main promise (constraint #7) is broken for anyone using it now. It needs nothing else and gives SG-14 its `gate_findings` building block.
- **07 → 11 → 16 → 12 → 13** make delivery trustworthy, in order: no lost webhooks, no burned retries, no stuck messages, one shared queue, a bounded orchestrator.
- **14** connects it all. It's the first time a real webhook produces a real review.
- **24** before the features: once real reviews flow, measure precision *before* tuning anything.
- **19, 20** are what users feel: triage held-back findings, inline one-click fixes, no repeated comments.
- **21, 22, 23** cover scale and depth: deploy, multi-worker, semantic cross-file reasoning.

## Key finding of the 2026-11-26 review
None of `AggregatorAgent`, `router.route()` and `ApprovalQueueStore` has a production call site. Only tests call them. Every non-negotiable constraint about confidence and approval is verified **in unit tests only**. SG-17 and SG-14 make them true at runtime. Until then, the README's constraint table should be read as design intent, not as current behaviour.
