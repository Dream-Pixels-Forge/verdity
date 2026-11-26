# SG-14 — Connect the webhook pipeline end to end

**Severity:** Critical · **Size:** L · **Depends on:** SG-07, 11, 12, 13, 16, 17 · **Branch:** `feat/sg-14-e2e-pipeline`

## Goal
A real PR webhook should end with a real review on the PR. Today the production path runs specialists over **nothing** and then throws the results away.

## Evidence (checked 2026-11-26)
- **No diff.** The gateway enqueues the normalised event only. `Orchestrator.process_event` reads `envelope.diff_files` (always `[]` from the gateway) and passes it to specialists. Only the MCP/CLI path calls `GitHubClient.get_pr_diff()`. Every specialist in the worker scans an empty change set.
- **No aggregation, routing or approval queue.** `AggregatorAgent`, `router.route()` and `ApprovalQueueStore` are never instantiated or called from `orchestrator.py`, `worker.py`, `mcp_server.py` or `cli/`. Only tests call them. Constraints #5 and #7 hold in unit tests but not in production.
- **No output.** The worker never creates a check run or a comment. The findings live in `Orchestrator._runs` until the process dies.
- **Platforms.** `platforms/{github,gitlab,bitbucket}.py` normalise webhooks, but none of them is used to *fetch the diff* or *post results* from the worker.

## Target flow
```
consume → fetch diff (platform adapter, by head_sha)
        → orchestrator fan-out (specialists)
        → AggregatorAgent.aggregate  (dedup / conflict / rank)
        → router.route per finding    (deterministic confidence + enforcement)
             AUTO_APPROVE  → postable set
             MANUAL_REVIEW → ApprovalQueueStore.enqueue
             AUTO_DISMISS  → audit only
        → stale-check: is head_sha still the PR head?  (SG-11 note)
        → PlatformPoster.publish(postable)  (check run + summary)
        → audit every decision  → ack
```

## Plan (TDD)
1. **Red:** one integration test (`tests/test_e2e_pipeline.py`) uses a fake platform adapter that serves a fixture diff containing a hard-coded AWS key and records posts. Run: gateway POST → worker drains one message → assert:
   - the fake adapter received exactly one check run, which includes the secret finding;
   - a medium-confidence finding is in `ApprovalQueueStore` and **not** in the post;
   - the audit log contains `run_started`, `finding.routed` (×N) and `run_completed`;
   - if the PR head moved while the run was in progress (the fake returns a new SHA), nothing is posted and the run is audited as `superseded`.
2. **Green**
   - Extend the `PlatformAdapter` protocol (`platforms/base.py`) with `fetch_diff(event)`, `current_head(event)` and `publish(event, postable, summary)`. Implement it for GitHub first. GitLab and Bitbucket can raise `NotImplementedError` behind a feature flag.
   - Add a `ReviewPipeline` that composes the orchestrator, aggregator, router, approval queue and adapter, so `Orchestrator` stays focused on fan-out. Reuse it from MCP/CLI (SG-17 already moves them onto the router).
   - Have the worker build `ReviewPipeline` from `Settings` (SG-12 factory).
3. Add a short ADR to `dev-notes/05-Agent-Orchestration-Design.md` describing the composed pipeline.

## Definition of done
- [ ] The integration test passes in CI on all three Python versions.
- [ ] `grep -rn "AggregatorAgent\|router.route\|ApprovalQueueStore(" src/verdity` shows production call sites.
- [ ] A manual smoke test against a sandbox repo produces a real check run (record the run URL in the PR).

## Commit
`feat(pipeline): wire webhook → diff → specialists → aggregate → route → approval/post`
