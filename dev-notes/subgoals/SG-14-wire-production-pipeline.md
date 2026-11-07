# SG-14 — Connect the webhook pipeline end to end: fetch diff, aggregate, route, approval queue, post

**Severity:** Critical (the hosted service does no review today) · **Size:** L · **Depends on:** SG-07–SG-13 · **Branch:** `feat/sg-14-wire-pipeline`

## Goal
A signed `pull_request.opened` webhook leads to a real review: the diff is fetched, specialists run on it, findings are deduped, scored and routed, and results are posted or queued for approval, as the README architecture describes.

## Evidence
- `orchestrator.py:235`: `run.diff_files = envelope.diff_files`. The gateway never sets it, and `EventQueue.publish` only stores `event` and `enqueued_at` (`event_queue.py:81`), so `diff_files` and `tier` would be lost even if they were set. **Specialists run on an empty diff.**
- `Router(`, `Aggregator(` and `ApprovalQueueStore(` aren't instantiated anywhere in `src/` outside their own modules. `process_event` ends after adversarial review and metrics; nothing routes or posts.
- The working path today is CLI/MCP (`verdity-review` → `mcp_server` → `GitHubClient`, `mcp_server.py` around line 912). Reuse it instead of writing a second implementation.
- Non-negotiable constraints 5, 7 and 9 (deterministic confidence, sub-threshold findings never auto-posted, everything audited) must hold on this path.

## Plan (TDD) — split into small PRs if it gets large
1. **14a — Diff fetching.** Add a `DiffProvider` protocol injected into the `Orchestrator`, with a GitHub implementation using `GitHubClient.get_pr_diff` (GitLab and Bitbucket clients follow the same interface). When the envelope has no `diff_files`, fetch them. Also resolve the GitLab base SHA here (see SG-10).
   - **Red:** with a fake provider, specialists receive non-empty `ctx.diff_files`.
2. **14b — Aggregate and route.** After the specialists and adversarial review: `Aggregator` (dedup and ranking), then `Router` (deterministic score to auto-post, manual review or dismiss), then `ApprovalQueueStore` for manual items. Audit every decision.
   - **Red:** a sub-threshold finding goes to the approval queue and is never posted (constraint 7).
3. **14c — Publish.** Add a `ReviewPublisher` protocol (GitHub: review comments plus a check run; reuse the MCP code by moving it into a shared module). Respect `post_to_github`-style config, with a dry-run default.
   - **Red:** auto-approved findings are posted once; a retried run doesn't double-post (key on run plus finding id).
4. **14d — Worker wiring.** `_run_worker` builds the provider, publisher, router and approval queue from settings.
5. **14e — End-to-end test:** a signed webhook to the gateway on a temp DB, a worker on the same DB, fake GitHub (respx or an `httpx.MockTransport`), and assertions on the posted review payload and the audit rows.
6. Update the README architecture section so it matches what's actually wired.

## Definition of done
- [ ] The E2E test passes: webhook → posted review (mocked GitHub) with an audit trail.
- [ ] Constraints 5, 7 and 9 each have an assertion in the E2E test.
- [ ] There's no duplicate posting code between the MCP and worker paths.

## Commit(s)
`feat(orchestrator): fetch PR diff via DiffProvider` · `feat(orchestrator): aggregate, route and queue findings` · `feat(worker): publish routed findings to platform` · `test: end-to-end webhook to review`
