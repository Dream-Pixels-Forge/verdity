# Technical Architecture Document
## Verdity — AI Pull Request Reviewer, Production Agent System

**Status:** Draft v1.0
**Last updated:** 2026-08-23
**Project name:** Verdity (verdict + integrity). Root package/namespace: `verdity` (e.g. `verdity-gateway`, `verdity-orchestrator`, `verdity-agents-security`).

---

## 1. System Overview

```
GitHub ──webhook──▶ Ingestion Gateway ──▶ Event Queue ──▶ Orchestrator
                     (HMAC verify,           (durable,        │
                      ack <1s)                idempotent)     ├──▶ Semantic Index Service (shared)
                                                                │
                                              ┌─────────────────┼─────────────────┐
                                              ▼                 ▼                 ▼
                                        Security Agent   Code Quality Agent  Testing Agent  Docs Agent
                                              │                 │                 │            │
                                              └────────┬────────┴────────┬────────┴────────────┘
                                                        ▼                 
                                                 Aggregator Agent
                                                        │
                                        ┌───────────────┼───────────────┐
                                        ▼                               ▼
                              Confidence Threshold Router      Audit/Findings Store
                                        │
                         ┌──────────────┴──────────────┐
                         ▼                              ▼
                 Auto-post to GitHub            Approval Queue (Human-in-loop)
                                                          │
                                                          ▼
                                                  Approved → GitHub

Parallel path for code-changing agents:
Coding Agent ──▶ Verification Gate ──▶ Independent Verifier Subagent ──▶ Regression Runner ──▶ Ready state

Cross-cutting: Token Economics Service (meters every model call) ──▶ Real-time Dashboard + Budget Enforcer
```

## 2. Components

### 2.1 Ingestion Gateway
- Stateless HTTP service receiving GitHub webhook POSTs.
- Verifies `X-Hub-Signature-256` (HMAC-SHA256 over raw body) before touching payload.
- Verifies delivery is not a replay (delivery ID dedupe cache, TTL 24h).
- On success: publishes a normalized event to the Event Queue and returns `202` immediately. No processing happens in the request/response cycle — this is the decoupling boundary.
- On signature failure: returns `401`, logs, does not queue.

### 2.2 Event Queue
- Durable, at-least-once queue (e.g., SQS, Cloud Pub/Sub, or Kafka depending on cloud).
- Partitioned by repo ID to preserve ordering per repo while allowing cross-repo parallelism.
- Dead-letter queue for events that fail processing after N retries, alertable.

### 2.3 Orchestrator
- Consumes events, resolves the **trigger taxonomy** (see PRD 5.1) into a **review policy** (which specialists, what depth, SLA).
- Owns the workflow graph: fan-out to specialists → fan-in to aggregator → threshold routing.
- Implemented as a workflow engine (e.g., Temporal, or a lightweight DAG runner) so that long-running, multi-step, retryable agent workflows have durable state — this is the "maintain project state" requirement, not an in-memory chain.
- Idempotent: re-delivery of the same event does not duplicate work (keyed by PR head SHA + trigger type).

### 2.4 Semantic Index Service (the "one database")
Consolidates what would otherwise be 3+ separate stores into a single service with multiple access patterns over one underlying store:
- **Embeddings** for semantic code search (function/class/file-level chunks, re-embedded incrementally on push).
- **Symbol graph** (call graph, import graph) for structural queries ("who calls this function," "what implements this interface").
- **Metadata cache** (file hashes, last-indexed SHA) to support incremental re-indexing — only changed files are re-embedded.

All specialist agents query this one service via a tool interface rather than each maintaining their own vector store, eliminating the "multi-database maintenance overhead" called out in the brief. Backing store: a single Postgres instance with `pgvector` (or equivalent) is sufficient at moderate scale and avoids running a separate vector DB, graph DB, and cache as three systems to operate.

### 2.5 Specialist Agents
Each specialist is an independently deployable agent with:
- A scoped system prompt and toolset (e.g., Security agent has a CVE-lookup tool and secret-scanner tool; Testing agent has a coverage-diff tool).
- Read access to the diff, full file context (via Semantic Index), and PR metadata.
- Output constrained to a **structured findings schema** (JSON, schema-validated) — no free-text-only output accepted.
- Runs are parallel and isolated: one specialist's failure/timeout does not block the others (orchestrator applies per-agent timeout + partial-result aggregation).

### 2.6 Aggregator Agent
- Consumes structured findings from all specialists for a given PR run.
- Dedupes overlapping findings (same file/line/concern from multiple agents), resolves conflicts by policy (e.g., security severity always wins tie-breaks), and produces the final ranked finding set plus a PR-level summary.
- Deterministic post-processing (dedupe/sort) is done in code, not by asking an LLM to "merge these," to keep aggregation auditable and reproducible.

### 2.7 Confidence Threshold Router
- Reads each finding's confidence score against the repo/org-configured threshold.
- Above threshold (and auto-post enabled) → posts directly via GitHub API.
- Below threshold → writes to Approval Queue with full context for human review.
- All routing decisions are logged to the Audit Store.

### 2.8 Approval Queue
- A queryable store + UI (or API) where sub-threshold findings wait.
- Reviewer actions (approve/edit/reject) are captured and fed back into a calibration dataset used to periodically re-tune thresholds and few-shot examples.

### 2.9 Coding Agent + Verification Gate + Independent Verifier + Regression Runner
For any agent that proposes a code change (not just a review comment):
1. **Coding Agent** produces a diff + rationale.
2. **Verification Gate**: a structured, machine-checkable spec of what "done" means for this change (compiles, lints, matches the described intent) — checked automatically before any LLM re-review.
3. **Independent Verifier Subagent**: a separate agent instance (different context, arguably different model) reviews the diff against the original requirement without seeing the coding agent's own justification, to avoid self-confirmation bias.
4. **Regression Runner**: executes the existing test suite plus any tests the change should have added; only a full pass moves the change to "ready."
This mirrors a real eng workflow: author → CI checks → independent reviewer → tests green → mergeable.

### 2.10 Token Economics Service + Dashboard
- Every model call (from every agent) is metered: tokens in/out, model, cost, PR ID, repo, org.
- Emits real-time metrics to a time-series store (e.g., Prometheus/Timescale) feeding a live dashboard: spend by repo/org/day, cost per PR, cost per finding, budget burn-down.
- Enforces hard budget caps: when a repo/org approaches its cap, the orchestrator degrades gracefully (drop to fewer specialists, smaller context windows, cheaper model tier) rather than failing silently or over-spending.

### 2.11 Audit/Findings Store
- Append-only log of every finding, every routing decision, every agent version + prompt hash + tool calls used to produce it.
- Backs the "auditable findings" requirement and supports compliance/regulated-customer needs.

## 3. Data Flow (Happy Path)

1. Developer pushes to a PR → GitHub sends `pull_request.synchronize` webhook.
2. Ingestion Gateway verifies HMAC, ACKs, publishes normalized event.
3. Orchestrator resolves trigger → review policy → fans out to Security, Code Quality, Testing, Docs agents in parallel.
4. Each specialist queries the Semantic Index for context, produces structured findings.
5. Aggregator dedupes/ranks/summarizes.
6. Confidence Router splits findings: high-confidence → posted immediately; low-confidence → Approval Queue.
7. Token Economics Service has been metering every call throughout; dashboard updates in real time.
8. Everything is written to the Audit Store keyed by PR head SHA.

## 4. Deployment & Infra

- **Compute**: containerized services (Kubernetes or serverless functions for the Ingestion Gateway specifically, given its need for fast cold-start and horizontal burst scaling).
- **Queue**: managed durable queue (cloud-native: SQS/PubSub, or self-hosted Kafka for higher throughput/ordering needs).
- **Workflow engine**: Temporal (or equivalent) for durable orchestration state — this is what lets a multi-hour, multi-agent review survive process restarts.
- **Data stores**: Postgres + pgvector (semantic index, metadata), object storage for large diffs/artifacts, time-series DB for metrics.
- **Secrets**: managed secret store (e.g., cloud KMS-backed secrets manager) for the GitHub webhook HMAC secret and GitHub App credentials, with rotation support.
- **Observability**: structured logging, tracing across the agent fan-out (one trace per PR review run), metrics dashboards (Grafana or equivalent).

## 5. Scaling Considerations

- Ingestion Gateway must handle webhook burst traffic (e.g., a large monorepo force-pushing) — horizontally scalable, stateless.
- Semantic Index re-embedding is the likely bottleneck at scale — incremental indexing (only changed files) is required, not full repo re-embedding per push.
- Specialist agents scale horizontally per event; orchestrator enforces per-repo concurrency limits to avoid overwhelming the Semantic Index or exceeding GitHub API rate limits.

## 6. Key Design Decisions

| Decision | Rationale |
|---|---|
| Single shared semantic index vs. per-agent stores | Avoids multi-database maintenance overhead; consistent context across specialists |
| Durable workflow engine vs. simple in-process orchestration | Multi-agent reviews are long-running and must survive restarts/retries |
| Deterministic aggregation code vs. LLM-merges-everything | Auditability and reproducibility of the final posted comment |
| Independent verifier subagent | Avoids an agent grading its own work; mirrors human review separation of duties |
| Hard budget caps with graceful degradation | Protects against runaway spend without silent failures |
