# Product Requirements Document (PRD)
## Verdity — AI Pull Request Reviewer, Production Agent System

**Status:** Draft v1.0
**Owner:** [Product/Eng Lead]
**Last updated:** 2026-08-23
**Project name:** Verdity (verdict + integrity) — every output is a structured, evidence-backed verdict delivered with calibrated integrity, never a hallucinated guess auto-posted without earning trust.

---

## 1. Problem Statement

Human PR review is slow, inconsistent, and unevenly applied across security, code quality, testing, and documentation concerns. Reviewers context-switch across specialties, findings aren't auditable, and there's no confidence signal telling a maintainer how much to trust a given comment. Teams that have tried "an LLM comments on the diff" bolt-ons get noisy, low-trust output with no guardrails on spend or hallucination.

This project builds **Verdity**, a **production-grade, multi-agent PR review system** that replicates and improves on a human review workflow: specialized reviewers working in parallel, an editor who reconciles their findings, and an approval gate before anything reaches a human.

## 2. Goals

1. Break the human review workflow into a formal model: **triggers → specialist concerns → auditable findings → confidence scores**.
2. Run **domain specialist agents** (security, code quality, testing, documentation) in parallel and aggregate their output deterministically.
3. Replace fragmented tooling (separate embeddings DB, separate metadata store, separate cache) with a **single semantic code index** the whole system shares.
4. Ingest GitHub webhooks **asynchronously and verifiably** (HMAC), decoupled from processing.
5. Keep coding/verification agents honest via **structured verification gates, independent verifier subagents, and automated regression checks** — no agent grades its own homework.
6. Prevent hallucinated findings and runaway cloud spend via a **confidence-threshold approval queue** and a **real-time token economics dashboard**.

## 3. Non-Goals

- Auto-merging PRs without human approval (the system recommends; humans/CI gate merges).
- Replacing human review entirely — this augments reviewers, it doesn't remove the approval step for anything below the confidence threshold... or above it, if the org disables auto-approve.
- Supporting non-GitHub SCMs in v1 (GitLab/Bitbucket are explicitly deferred).
- Fine-tuning custom models — v1 is built on top of frontier LLM APIs via prompting/tools.

## 4. Users & Personas

| Persona | Need |
|---|---|
| **Contributing engineer** | Fast, specific, low-noise feedback before a human looks at the PR |
| **Maintainer / tech lead** | Trustworthy triage — know which findings need their eyes vs. which are safe to auto-resolve |
| **Security team** | Guaranteed coverage of security-relevant diffs, with audit trail |
| **Eng leadership / FinOps** | Visibility into and control over LLM spend per PR, per repo, per month |
| **Platform/SRE** | A system that fails safe, is observable, and doesn't silently drop webhooks |

## 5. Core Workflow Requirements

### 5.1 Trigger Taxonomy
The system must classify each incoming event into a precise trigger type before doing any expensive work:

- `pr.opened`, `pr.synchronize` (new commits), `pr.ready_for_review` (draft → ready)
- `review_comment.created` (human asks the bot a follow-up)
- `check_suite.rerequested` (manual re-run)
- Path-based triggers (e.g., changes under `/infra` always trigger security specialist regardless of size)

Each trigger maps to a **review policy**: which specialists run, at what depth, and under what SLA.

### 5.2 Specialist Concerns
Each specialist agent owns a bounded concern and produces findings only within that scope:

- **Security**: authn/authz regressions, secrets, injection classes, dependency CVEs, unsafe deserialization, SSRF/path traversal, IaC misconfig.
- **Code Quality**: complexity, duplication, dead code, style/lint violations not caught by CI, anti-patterns, naming/readability.
- **Testing**: coverage deltas, missing edge cases, flaky-test risk, assertion strength, whether tests actually exercise the changed logic.
- **Documentation**: outdated docstrings/README/API docs relative to the diff, missing changelog entries, public API doc-comment completeness.

### 5.3 Auditable Findings
Every finding is a structured object, not free text, containing: file/line anchor, concern category, severity, natural-language explanation, suggested fix (optional diff), **confidence score (0–1)**, the evidence/tool calls that produced it, and a stable finding ID for traceability across re-runs.

### 5.4 Confidence Scoring
Confidence is a first-class, calibrated signal (see Agent Orchestration Design doc for methodology) — not a vibe. It drives the approval queue (5.6) and is logged for offline calibration against reviewer feedback (thumbs up/down, "resolved as intended" vs "dismissed as wrong").

### 5.5 Aggregation
An aggregator agent reconciles specialist findings: dedupes overlapping findings, resolves conflicting recommendations, orders by severity × confidence, and produces a single PR-level summary comment plus inline comments.

### 5.6 Approval Queue
Findings below the confidence threshold (configurable per repo/org) route to a **human-in-the-loop approval queue** before posting to the PR. Findings above threshold may auto-post (configurable). All queue decisions are logged and feed back into calibration.

### 5.7 Verification Gates
Any agent that proposes a code change (not just a comment) must pass through: (a) a structured verification gate defining pass/fail criteria, (b) an **independent verifier subagent** that did not write the change, and (c) automated regression checks (existing test suite + any newly required tests) before the change is presented as ready.

## 6. Functional Requirements

| ID | Requirement | Priority |
|---|---|---|
| FR-1 | Verify every inbound GitHub webhook via HMAC-SHA256 signature before enqueueing | P0 |
| FR-2 | Decouple ingestion from processing via a durable queue; ingestion ack's in <1s | P0 |
| FR-3 | Run security, code-quality, testing, documentation agents in parallel per PR | P0 |
| FR-4 | Maintain one semantic code index (embeddings + symbol graph) per repo, incrementally updated on push | P0 |
| FR-5 | Emit structured, schema-validated findings with confidence scores | P0 |
| FR-6 | Aggregate findings into a deduped, prioritized PR comment | P0 |
| FR-7 | Route sub-threshold findings to an approval queue UI | P0 |
| FR-8 | Track token spend per PR/repo/org in real time; enforce budget caps | P0 |
| FR-9 | Independent verifier subagent re-checks any agent-proposed code diff | P1 |
| FR-10 | Automated regression check runs before a proposed fix is marked ready | P1 |
| FR-11 | Full audit log: every finding traceable to trigger, agent version, prompt, tool calls, cost | P1 |
| FR-12 | Re-run / re-review on new commits without re-processing unchanged files | P1 |
| FR-13 | Org-level configuration: thresholds, enabled specialists, budget caps, auto-post rules | P2 |

## 7. Non-Functional Requirements

- **Latency**: initial findings posted within 5 minutes of `pr.opened` for PRs under 500 changed lines (P0 SLA).
- **Reliability**: webhook ingestion must not lose events; at-least-once processing with idempotent finding IDs.
- **Cost control**: hard per-PR and per-org token budget caps with graceful degradation (fewer specialists, smaller context) rather than silent failure.
- **Security**: webhook secret rotation without downtime; least-privilege GitHub App permissions; no PR code persisted beyond the retention window without consent.
- **Auditability**: every posted comment reconstructable from stored evidence — required for regulated customers.
- **Observability**: dashboards for queue depth, agent latency, confidence distribution, spend, approval-queue backlog.

## 8. Success Metrics

- % of findings marked "useful" by reviewers (target ≥80% at GA)
- Median time-to-first-comment
- False-positive rate on security findings (target <5%)
- Cost per reviewed PR (tracked, trending down via caching/index reuse)
- % of findings auto-posted vs. queued (health signal on calibration)
- Reduction in human review cycle time (measured via paired repos, pilot vs. control)

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Hallucinated findings erode trust | Confidence threshold + approval queue + independent verifier |
| Runaway LLM spend | Real-time budget dashboard + hard caps + degrade-gracefully policy |
| Webhook spoofing/replay | HMAC verification + timestamp/nonce checks |
| Duplicate multi-store maintenance overhead | Single semantic index as source of truth (see Architecture doc) |
| Specialist agents disagree | Deterministic aggregation/conflict-resolution policy owned by aggregator agent |
| Regression from agent-proposed fixes | Mandatory regression check gate before "ready" state |

## 10. Open Questions

- Do we support monorepos with per-directory ownership/policy in v1, or defer to v1.1?
- What's the default auto-post confidence threshold, and is it globally fixed or learned per repo?
- Data retention policy for source code sent to model providers — configurable per customer?
