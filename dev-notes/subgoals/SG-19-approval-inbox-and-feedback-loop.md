# SG-19 — Approval inbox API and the human feedback loop

**Type:** Feature · **Size:** M · **Depends on:** SG-14, SG-17 · **Branch:** `feat/sg-19-approval-inbox`

## Why
Once SG-17 holds medium-confidence findings back, someone has to *act* on them. Today `ApprovalQueueStore` has no HTTP, MCP or CLI surface for approve and dismiss, and there's no path from a human decision back into `TrustCalibrator`. The calibrator learns only from whatever `record_signal` calls tests make. Closing this loop is what makes the confidence scores *earned*. It's the "verdict + fidelity" product promise.

## Scope
1. **API** (gateway, bearer-token auth like `/metrics`; reuse SG-04's dependency):
   - `GET  /verdity/approvals?repo=&state=pending&limit=` lists items with finding, confidence, routing reason and SLA remaining.
   - `POST /verdity/approvals/{id}/approve` posts the finding to the PR (through `PlatformAdapter.publish`, as a follow-up annotation or comment) and records the trust signal `true_positive`.
   - `POST /verdity/approvals/{id}/dismiss` with `{reason: false_positive|wont_fix|duplicate}` records the matching signal.
   - Every decision is appended to `AuditStore` with the actor identity from the token (constraint #9).
2. **MCP tools:** `approvals_list`, `approval_decide`, so a reviewer can triage from Claude, Cursor and similar clients.
3. **CLI:** `verdity-approvals list|approve|dismiss`.
4. **PR-native decisions (stretch):** a reviewer replies `/verdity approve <id>` or reacts 👍/👎 on a posted finding. The gateway handles the `issue_comment` and `pull_request_review_comment` events (currently ignored by SG-09) as decisions, HMAC-verified like any webhook.
5. **Calibration hook:** each decision calls `TrustCalibrator.record_signal(finding_type, repo_id, outcome)`. The nightly recalibration (already in the orchestrator) then has real data.

## Plan (TDD)
- Red: approving an item causes exactly one publish call, one trust signal and one audit row. Approving it again gives 409. Dismissing it never publishes. An unauthenticated request gets 401.
- Red: after 50 `false_positive` decisions on rule X, `recalibrate()` lowers X's weight within the bounded range.
- Green: add a thin service, `ApprovalService(store, adapter, calibrator, audit)`, used by the HTTP, MCP and CLI routes.

## Definition of done
- [ ] The three surfaces share one service and are covered by tests.
- [ ] README section: "Triaging held-back findings".

## Commit
`feat(approvals): inbox API/MCP/CLI with decisions feeding trust calibration`
