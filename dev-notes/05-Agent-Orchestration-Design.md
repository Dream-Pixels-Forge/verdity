# Agent Orchestration Design
## Verdity — AI Pull Request Reviewer, Production Agent System

**Status:** Draft v1.0
**Last updated:** 2026-08-23
**Project name:** Verdity (verdict + integrity).

---

## 1. Purpose

This document specifies *how* agents are coordinated: the workflow patterns used for fan-out/fan-in, the confidence scoring methodology, verification-gate mechanics, and the approval-queue/budget control loop. It's the operational complement to the Technical Architecture Document.

## 2. Workflow Patterns Used

### 2.1 Parallel Fan-Out / Fan-In (Specialist Review)
The core review workflow is a **scatter-gather**: the orchestrator dispatches one task per specialist concurrently, waits (with per-agent timeout), and gathers whatever completes into the aggregator step. This is chosen over a sequential chain because specialist concerns are largely independent — security findings don't depend on documentation findings — so serializing them only adds latency without adding accuracy.

```
                ┌──▶ Security Agent ───────┐
Orchestrator ───┼──▶ Code Quality Agent ───┼──▶ Aggregator ──▶ Confidence Router
                ├──▶ Testing Agent ────────┤
                └──▶ Documentation Agent ──┘
```

### 2.2 Evaluator-Optimizer (Coding Agent path)
For agents that *propose changes* rather than just comment, a different pattern applies: **generate → verify → (loop if failed) → regress → ready**. This is a tighter loop than the review fan-out because a rejected verification should trigger a bounded number of retries, not a silent failure.

```
Coding Agent ──▶ Verification Gate ──fail──▶ (retry, max N) ──▶ Coding Agent
       │                  │pass
       │                  ▼
       │         Independent Verifier ──fail──▶ Approval Queue (escalate to human)
       │                  │pass
       │                  ▼
       └────────▶ Regression Runner ──fail──▶ Approval Queue (escalate to human)
                          │pass
                          ▼
                    Ready state
```
Bounding retries (e.g., max 3 attempts) is deliberate: an agent that can't pass verification after bounded retries should escalate to a human, not loop indefinitely and burn budget.

### 2.3 Orchestrator-Worker with Durable State
The orchestrator itself is implemented as a durable workflow (not a stateless function call chain) so that a review spanning multiple agent calls, human approval waits, and retries survives process restarts and can be resumed, inspected, and audited mid-flight. Each PR review run is one workflow execution, keyed by `review_run_id`.

### 2.4 Routing (Confidence Threshold)
A simple conditional router, not an agent decision — deterministic code compares each finding's `confidence` field against the configured threshold and branches. Keeping this deterministic (not "ask an LLM whether to post") is intentional: the safeguard against hallucination shouldn't itself be delegated to the model being safeguarded against.

## 3. Trigger → Policy Mapping

| Trigger | Specialists Run | Depth | SLA |
|---|---|---|---|
| `pr.opened` (small, <200 lines) | All four | Standard | 5 min |
| `pr.opened` (large, ≥200 lines) | All four | Extended context, chunked | 15 min |
| `pr.synchronize` | Only specialists whose relevant files changed (delta-aware) | Standard | 5 min |
| Path match: `/infra/**`, `/auth/**` | Security forced-on regardless of size/delta | Extended | 5 min |
| `review_comment.created` (bot mentioned) | Single relevant specialist, conversational context | Fast | 2 min |
| `check_suite.rerequested` | All four, full re-run | Standard | 5 min |

Delta-awareness on `pr.synchronize` matters at scale: re-running all four specialists on every incremental push (which can be dozens per PR) is both slow and expensive. The orchestrator diffs the new commits against the last-reviewed SHA and only re-invokes specialists whose concern-relevant files actually changed.

## 4. Confidence Scoring Methodology

Confidence is **not** simply the model's self-reported certainty (LLMs are poorly calibrated when asked directly "how confident are you"). It's computed from multiple signals:

| Signal | Weight rationale |
|---|---|
| Evidence strength | A finding backed by a tool result (e.g., CVE lookup, actual test-coverage diff) scores higher than one backed only by the model's read of the code |
| Historical precision for this finding type | Tracked per (specialist, concern-subcategory) from approval-queue outcomes — categories with high historical false-positive rates get down-weighted |
| Severity/ambiguity of the concern itself | Some concern types (hardcoded secret detected by a regex-backed tool) are near-binary and score high; some (naming could be clearer) are inherently subjective and capped lower |
| Cross-agent corroboration | If two independent specialists flag overlapping evidence for the same region of code, confidence is boosted during aggregation |

Confidence is computed by the aggregator as a function of these signals (a small classifier or weighted rule-set trained/tuned on approval-queue decision history), not asserted directly by the specialist LLM call. This keeps the score auditable and independently improvable without re-prompting every specialist.

**Calibration loop**: every approval-queue decision (approve/edit/reject) and every reviewer reaction on an auto-posted comment (👍/👎, resolved vs. dismissed) is logged and periodically used to re-tune the confidence function per concern-subcategory. This is an offline batch job, not real-time, to avoid feedback-loop instability.

## 5. Verification Gate Specification

A verification gate is a **structured, machine-checkable definition of "done"** for a proposed change, distinct from an LLM's opinion:

```json
{
  "gate_id": "uuid",
  "checks": [
    { "type": "compiles", "required": true },
    { "type": "lint_pass", "required": true },
    { "type": "matches_intent", "required": true, "method": "llm_judge", "rubric": "..." },
    { "type": "no_new_secrets", "required": true, "method": "static_scan" }
  ]
}
```
`compiles`, `lint_pass`, and `no_new_secrets` are deterministic tool checks. `matches_intent` is the one LLM-judged check in the gate — and it's judged by the **independent verifier subagent**, never by the coding agent itself, precisely because self-assessment is unreliable (see Security doc §4.3).

## 6. Independent Verifier Subagent Design

- Invoked with: the proposed diff, the original requirement/finding it addresses, and full repo context via the Semantic Index. **Not** given the coding agent's chain-of-thought or self-justification.
- Produces a structured verdict: `{ "verdict": "pass" | "fail", "reasons": [...], "confidence": 0.0-1.0 }`.
- A `fail` verdict routes to the Approval Queue rather than back to the coding agent automatically beyond the bounded retry count (§2.2) — repeated automatic retries on a fundamentally misunderstood requirement waste budget without converging.

## 7. Regression Check Automation

- Default: run the test suite scoped to **affected** tests (via the Semantic Index's call graph — which tests exercise the changed functions/files) for fast feedback.
- Before marking a change definitively "ready" for human merge: run the **full** suite as a final gate, since affected-test scoping can miss indirect regressions.
- Any test failure blocks "ready" state and routes to the Approval Queue with the failure output attached — this is a hard gate, not a suggestion.

## 8. Approval Queue Mechanics

- Populated by: (a) sub-confidence-threshold specialist findings, (b) failed independent-verifier verdicts, (c) failed regression runs, (d) any budget-degraded run that produced incomplete coverage.
- Queue items carry full context: original trigger, all evidence, confidence score and *why* (which signals drove it down), and — for coding-agent items — the verifier's stated reasons for failure.
- Human decisions (approve/edit/reject) write back into the calibration dataset (§4) and, for coding-agent items, either release the change to "ready" or close it out as rejected.

## 9. Token Economics Control Loop

```
Every model call ──▶ Token Economics Service (meters tokens/cost, tags with review_run_id, repo, org)
                              │
                     Real-time aggregation
                              │
              ┌───────────────┴───────────────┐
              ▼                                ▼
     Dashboard (spend by repo/org/day,   Budget Enforcer
     cost per PR, cost per finding)      (compares live spend to configured caps)
                                                │
                                    warn threshold ──▶ alert only
                                    hard threshold ──▶ signal orchestrator to degrade:
                                                         - drop optional specialists (e.g., docs) first
                                                         - reduce context window / chunk size
                                                         - fall back to a cheaper model tier
                                                         - as last resort: queue-only mode (no auto-post)
```
Degradation order is configurable per org but defaults to preserving **security** coverage last — if a budget must be cut, security is the last specialist dropped.

## 10. Failure Modes & Handling Summary

| Failure | Handling |
|---|---|
| One specialist times out | Orchestrator proceeds with partial results; summary comment notes incomplete coverage |
| Aggregator conflict between specialists on the same code region | Deterministic tie-break policy (severity wins; documented in Architecture §2.6) |
| Verifier subagent disagrees with coding agent repeatedly | Bounded retries, then escalate to Approval Queue — never infinite-loop |
| Regression run fails | Hard block on "ready" state, routed to Approval Queue with failure output |
| Budget cap hit mid-run | Graceful degradation per §9, never silent partial output presented as complete |
