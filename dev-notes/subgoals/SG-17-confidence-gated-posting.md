# SG-17 — Gate every post through the router (constraint #7 is broken today)

**Severity:** Critical (core promise) · **Size:** M · **Depends on:** — (can start now) · **Branch:** `fix/sg-17-gated-posting`

## Goal
*"Sub-threshold findings never auto-post; they always go to the Approval Queue."* That's non-negotiable constraint #7 in the README, and Verdity's main claim ("never auto-posted without earning it"). Make it true on **every** path that writes to a PR.

## Evidence (checked 2026-11-26)
- `mcp_server.py` (`verdity_review`, ~line 1026): when `post_to_github=True`, **every** finding is converted and posted as a check run (`postable.append(...)` for all `findings`). There's no `compute_confidence`, no `route()` and no approval queue.
- `verdity-review --post-check/--post-comment` (`cli/review.py:88`) goes through the same MCP tool, so it has the same bypass.
- `router.route()` and `ApprovalQueueStore` have no production callers. Only tests call them (see SG-14 evidence).
- The `AggregatorAgent` (dedup/conflict) isn't applied either, so duplicate findings from two specialists are posted twice.
- An enforcement `BLOCK` maps to `RouteAction.MANUAL_REVIEW` (`router.py`, `action_map`). A "blocking" rule never fails the check run; it just queues the finding. Blocking and needing approval should be distinct outcomes.

## Plan (TDD)
1. **Red** (`tests/test_gated_posting.py`)
   - An MCP `verdity_review` with `post_to_github=True` on a diff that produces one high-confidence finding and one low-confidence finding: the mocked `create_check_run` receives **only** the high one. The medium one is in `ApprovalQueueStore`, the low one is audited as dismissed.
   - Two specialists reporting the same `(file, line, rule_id)` result in one posted annotation.
   - A `BLOCK` enforcement rule gives a check-run `conclusion="failure"`, and the summary names the rule.
   - The response JSON includes `routing: {posted, queued, dismissed}` counts so CLI users can see what was held back.
2. **Green**
   - Add one function, `gate_findings(findings, *, calibrator, enforcement, approval_queue, audit) -> GateResult(postable, queued, dismissed, blocking)`, in `router.py` (or a new `gating.py`). It runs aggregate → route → enqueue → audit.
   - MCP and CLI call it before any post. SG-14's `ReviewPipeline` reuses it, so there's one implementation.
   - Add `RouteAction.BLOCK`. The check-run conclusion becomes `failure` if any finding is `blocking`, `neutral` if any are queued, and `success` otherwise.
   - Add a `--post-all` escape hatch for local experiments only. It's off by default, always audited, and prints a warning.
3. Update the README constraint table: change the verification column for #7 from "Router routes by score threshold" to "`gate_findings` on every post path + `test_gated_posting.py`".

## What was done
- `src/verdity/gating.py`: `gate_findings(...) -> GateResult(postable, queued, dismissed, blocking)`. It aggregates with `confidence_threshold=0.0`, because the aggregator's raw 0.7 filter would otherwise drop CRITICAL findings that the router scores at 1.0. Each finding then goes through `router.route`, MANUAL_REVIEW findings go to `ApprovalQueue.enqueue`, and every finding gets a `finding.routed` audit record. Non-`Finding` results are skipped and counted, never posted.
- MCP `verdity_review` gates **every** review, posted or not. The response adds `routing`, `queued_findings` and `conclusion`, and the CLI text output prints the counts. The check run is created as `completed` with a real `conclusion` (before, it stayed `in_progress` forever), and its summary lists what was held back. The MCP server now owns an `ApprovalQueue` (in-memory, like its other stores).
- `RouteAction.BLOCK` was added: an enforcement `BLOCK` gives a posted finding and `conclusion="failure"`, while `REQUIRE_APPROVAL` and `ESCALATE` still give MANUAL_REVIEW.
- **Second ungated path found by the guard test:** `CodingAgent.apply_fix_and_open_pr` called `post_pr_review`. It's now gated. The method itself is a stub that comments on a hard-coded PR #1 and reports fake success; that's tracked in SG-25.
- Removed `_finding_from_flattened`, the lossy dict→Finding round-trip that was only used for posting.
- **Dropped from the plan:** the `--post-all` escape hatch. It would reintroduce the exact bypass this subgoal removes. The JSON output already shows every finding, including held and dismissed ones.

## Follow-ups (not in scope)
- MCP passes no `enforcement` engine or `calibrator` yet, so `BLOCK` and calibrated weights only apply once a caller wires them in. SG-14's `ReviewPipeline` should load `.verdity/rules.yml` and the `TrustCalibrator`.
- The MCP approval queue is in-memory, so held findings from a one-shot CLI run are visible in the output but not persisted. SG-19 gives them a durable store and an inbox.

## Definition of done
- [x] No code path posts to a PR without `gate_findings`. `test_every_pr_post_call_site_is_gated` walks the AST of `src/` and fails on any function that calls a post method without it.
- [x] The Red tests pass (`tests/test_gated_posting.py`, 13 tests), and coverage stays at 100%.

## Commit
`fix(routing): gate all PR posts through aggregate→route→approval queue (constraint #7)`
