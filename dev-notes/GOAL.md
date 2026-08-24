# GOAL.md

## Build Charter — Verdity (AI Pull Request Reviewer, Production Agent System)

**Project name: Verdity** — from "verdict" + "fidelity/integrity." The name reflects the two properties this system must never compromise on: every finding is a **verdict** (structured, evidence-backed, confidence-scored — not a vague comment), delivered with **integrity** (calibrated, gated, auditable, never hallucinated or auto-posted without earning it). Keep this in mind as a tie-breaker whenever a design or naming decision is ambiguous: does this choice make the output more like a trustworthy verdict, or less?

**Read this file first, and re-read it before starting any new phase or after any context reset.**
This file is the single source of truth for scope and sequencing. If anything in a conversation, a passing idea, or an intermediate step conflicts with this file, **this file wins**. Do not silently expand scope, rename components, or introduce new architecture that isn't traceable back to one of the reference documents below.

---

## 0. Reference Documents (authoritative — do not contradict these)

inside `verdity/dev-notes` folder:

| Doc                                     | Answers                                                                               |
| --------------------------------------- | ------------------------------------------------------------------------------------- |
| `01-Product-Requirements-Document.md`   | What are we building and why? What's in/out of scope? What defines success?           |
| `02-Technical-Architecture-Document.md` | What are the components, how do they connect, what's the tech stack?                  |
| `03-API-Webhook-Specification.md`       | What are the exact request/response contracts, schemas, and endpoints?                |
| `04-Security-Threat-Model.md`           | What must never be violated (HMAC, least privilege, tenant isolation, secrets)?       |
| `05-Agent-Orchestration-Design.md`      | How do agents coordinate, how is confidence computed, how do verification gates work? |

**Rule: before writing any component, locate it in these docs. If it isn't there, stop and flag it rather than inventing it.** If a genuinely new requirement emerges mid-build, update the relevant doc first, then build — the docs and the code must never diverge.

---

## 1. One-Sentence Mission

Build **Verdity**, a production-ready, multi-agent system that reviews GitHub pull requests in parallel across security/quality/testing/docs, produces auditable confidence-scored findings, and never auto-posts a low-confidence or unverified finding without a human in the loop.

## 2. Non-Negotiable Constraints (violating any of these is a build failure, not a style choice)

1. **Every** inbound webhook is HMAC-SHA256 verified over the raw body with constant-time comparison before any parsing (API spec §1.3). No endpoint skips this, including in dev/staging — use a dev-only secret, never a bypass flag.
2. Ingestion and processing are **decoupled** via a durable queue. The webhook handler must never call an LLM, hit the Semantic Index, or do anything synchronous beyond verify+enqueue+ack.
3. Specialist agents run **in parallel**, not sequentially, and one specialist's timeout/failure must never block the others.
4. There is **one** semantic index/data store serving all specialists — do not let a specialist agent spin up its own vector store, cache, or metadata table "just for this feature."
5. Confidence scores are computed by **deterministic post-processing code**, never by asking the LLM "how confident are you" and trusting the number verbatim (Orchestration doc §4).
6. Any agent-proposed **code change** (not a comment) must pass: verification gate → independent verifier subagent (different context, no visibility into the author's reasoning) → regression run, in that order, before it can be marked ready.
7. Findings below the confidence threshold **never** auto-post to GitHub — they go to the Approval Queue, no exceptions, no admin override that skips logging.
8. Every model call is metered by the Token Economics Service. No agent, specialist or coding, may call a model provider through an unmetered path.
9. Every finding and every approval-queue decision is written to the append-only Audit Store. If it isn't logged, it didn't happen, and that's a build failure.

If you (the building agent) find yourself about to violate one of these nine to "get something working faster" — stop. A fast version that violates these is not a smaller version of this project; it's a different, unsafe project.

## 3. Definition of Production-Ready

The project is **not done** at "the demo works." It is done when all of the following are true:

- [ ] Ingestion Gateway rejects invalid/replayed webhooks (401/409) and passes valid ones through in <1s, verified under load.
- [ ] All four specialist agents (security, code quality, testing, documentation) run in parallel and produce schema-valid findings, verified against real PRs of varying size.
- [ ] Semantic Index does incremental (not full-repo) re-indexing on push.
- [ ] Confidence router correctly splits findings at the configured threshold; approval queue is reachable and functional end-to-end.
- [ ] Coding-agent path (if built in this phase) enforces verification gate → independent verifier → regression run, with bounded retries and escalation on repeated failure.
- [ ] Token economics dashboard shows live spend and budget enforcement actually degrades behavior when a cap is hit (test this — don't assume the cap "would" work).
- [ ] Audit Store has a complete, queryable trail for a sample PR review from webhook receipt to posted comment.
- [ ] Security & Threat Model §3 (STRIDE table) has been checked off item by item against the actual implementation, not just the design.
- [ ] All secrets (webhook HMAC, GitHub App key, model provider keys) are in a managed secret store, not in code, config files, or environment defaults checked into git.
- [ ] The system degrades gracefully (documented behavior, not a crash) under: specialist timeout, GitHub API rate limit, budget cap breach, queue backpressure.

None of these are "nice to have later." A build that skips any of them is incomplete, regardless of how polished the demo looks.

## 4. Build Order (do not reorder without a reason recorded in this file)

Build in this sequence. Each phase has a gate — do not start the next phase until the current phase's gate passes.

### Phase 1 — Ingestion & Contracts

Build: Ingestion Gateway (HMAC verify, ack, enqueue), Event Queue, the normalized internal event schema (API spec §2).
**Gate**: valid GitHub webhook → verified → queued → visible on the queue, in under 1s, with a real GitHub App test installation. Invalid signature → rejected, nothing queued.

### Phase 2 — Semantic Index

Build: the shared index service (embeddings + symbol graph + metadata cache), incremental indexing on push.
**Gate**: a query for "functions related to X" returns correct results on a real test repo, and a second push only re-embeds changed files (verify via logs/metrics, not assumption).

### Phase 3 — Orchestrator + One Specialist

Build: the durable workflow orchestrator, trigger taxonomy → policy mapping (Orchestration doc §3), and **one** specialist agent end-to-end (recommend: security, since it's forced-on for sensitive paths and exercises the most tooling).
**Gate**: a real PR triggers the orchestrator, the security agent runs, produces a schema-valid finding with evidence, and it's visible in the Audit Store.

### Phase 4 — Remaining Specialists + Aggregator

Build: code quality, testing, documentation agents; the aggregator (dedupe, conflict resolution, ranking).
**Gate**: a PR touching multiple concern areas produces a single deduped, ranked, correctly-attributed finding set — verify overlapping findings from two specialists actually get merged, not duplicated.

### Phase 5 — Confidence Router + Approval Queue

Build: confidence scoring function (multi-signal, per Orchestration doc §4 — not raw model self-report), router, approval queue store + minimal UI/API.
**Gate**: a deliberately low-confidence finding lands in the queue, not on GitHub. A deliberately high-confidence one posts automatically. A human decision in the queue is logged and feeds the calibration dataset.

### Phase 6 — Coding Agent Path (if in scope for this build)

Build: coding agent, verification gate, independent verifier subagent, regression runner.
**Gate**: a proposed fix that fails regression is blocked from "ready" state and lands in the approval queue with the failure attached. A verifier subagent disagreement is escalated, not silently overridden.

### Phase 7 — Token Economics + Budget Enforcement

Build: metering on every model call, real-time dashboard, budget caps with graceful degradation.
**Gate**: artificially lower a budget cap in a test environment and confirm the orchestrator actually degrades (fewer specialists / cheaper model / queue-only) rather than erroring or overspending.

### Phase 8 — Hardening & Production Readiness

Work through Section 3 checklist item by item. Load-test ingestion. Run the full STRIDE table from the Security doc as a literal checklist against the running system, not the design doc.

## 5. Anti-Drift Rules for the Building Agent

- **Don't rename components mid-build.** If the docs say "Aggregator Agent," the code says `AggregatorAgent`, not `MergerService` or `ReviewCombiner`. Consistent naming keeps the docs and code auditable together.
- **Namespace/package convention**: root package name is `verdity` (npm: `verdity`, PyPI: `verdity`, GitHub org/repo: `verdity`). Sub-components live under it — e.g. `verdity-gateway`, `verdity-orchestrator`, `verdity-agents-security` — rather than inventing unrelated product names for individual services.
- **Don't add a specialist, a data store, or a new external dependency that isn't in the Technical Architecture Document** without first adding it there and stating why.
- **Don't collapse the verification gate → independent verifier → regression run into one step** "for simplicity." This sequence exists specifically to avoid self-confirmation bias (Security doc §4.3) — it's a safety property, not an implementation detail.
- **Don't let the confidence threshold become a suggestion.** If a shortcut requires bypassing the approval queue "just for testing," build a separate test-mode flag that's clearly logged as test-mode — never quietly lower enforcement in what becomes the production path.
- **Don't optimize away the audit log** because it feels like overhead in an early phase. Retrofitting audit logging after the fact means the early phases are permanently unauditable.
- **If a requirement is ambiguous, re-check the PRD and Architecture docs before guessing.** If still ambiguous after that, make the smallest reasonable assumption, write it down in this file under a new "Assumptions Log" section (add one if it doesn't exist), and proceed — don't stall waiting for clarification that may not come.
- **At the end of every phase, re-read Section 2 (Non-Negotiable Constraints) before marking the phase done.** This is the fastest check against silent scope drift.

## 6. What "Done" Is Not

- Not done: a demo that reviews one hand-picked PR successfully.
- Not done: specialists that run but whose findings aren't schema-validated.
- Not done: a confidence score that's just the model saying "0.9" because it was asked to.
- Not done: an approval queue that exists in the schema but nothing actually routes to it.
- Not done: a dashboard that displays numbers but doesn't actually gate spend.
- Not done: HMAC verification that exists in one environment but is bypassed in another "temporarily."

## 7. Assumptions Log

| # | Date | Phase | Assumption | Why |
|---|------|-------|-----------|-----|
| 1 | 2026-08-23 | 1 | SQLite-backed durable queue for dev (swappable to Redis in prod) | No aiosqlite available in environment; stdlib sqlite3 + asyncio.to_thread provides identical semantics. Architecture doc §2.2 permits either. |
| 2 | 2026-08-23 | 1 | In-memory delivery-id dedupe cache for dev (24h TTL in prod via Redis) | Architecture doc §2.1 says "short-TTL dedupe cache"; in-memory set is sufficient for single-process dev. |
| 3 | 2026-08-23 | 1 | Audit Store uses same SQLite backend as queue for dev | Constraint #9 requires audit logging from day one; same DB engine avoids adding a second dependency. |
| 4 | 2026-08-23 | 1 | `datetime.now(timezone.utc)` instead of `datetime.utcnow()` | Python 3.14 deprecates `utcnow()`; timezone-aware datetimes are the correct modern approach. |
| 5 | 2026-08-23 | 1 | Unknown webhook event types fall back to `TriggerType.PR_OPENED` rather than crashing | Normalization must never reject a well-signed webhook; the trigger taxonomy is extended by the orchestrator (Phase 3). |
| 6 | 2026-08-23 | 3 | Orchestrator uses in-memory `dict[UUID, ReviewRun]` for workflow state | Sufficient for dev/single-process; production swaps to a durable engine (Temporal/Redis) per Architecture doc §2.3. State is fully reconstructible from Audit Store. |
| 7 | 2026-08-23 | 3 | Security agent uses deterministic regex/substring rules in dev; LLM calls are stubbed | Phase 3 gates on schema-valid findings with evidence — LLM integration is a content detail, not a structural property. Token metering still applies to all paths. |
| 8 | 2026-08-23 | 8 | Production deployment will use a managed KMS for secrets; this repo validates that secrets are read from env vars only | Secret rotation and KMS integration are deployment concerns; the code enforces the env-var requirement via pydantic-settings |
| 9 | 2026-08-23 | 8 | Prompt injection mitigation relies on Pydantic schema enforcement + system prompt discipline | Agents cannot "obey" injected instructions because output is structurally constrained to Finding objects |
