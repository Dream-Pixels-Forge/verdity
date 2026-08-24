# API & Webhook Specification
## Verdity — AI Pull Request Reviewer, Production Agent System

**Status:** Draft v1.0
**Last updated:** 2026-08-23
**Project name:** Verdity (verdict + integrity).

---

## 1. Inbound: GitHub Webhooks

### 1.1 Events Consumed

| Event | Action(s) | Purpose |
|---|---|---|
| `pull_request` | `opened`, `synchronize`, `reopened`, `ready_for_review` | Trigger a new or incremental review |
| `pull_request_review_comment` | `created` | Handle follow-up questions directed at the bot |
| `check_suite` | `rerequested` | Manual re-run trigger |
| `push` | (default branch only) | Incremental re-index of the Semantic Index |
| `installation` / `installation_repositories` | `created`, `deleted` | GitHub App install/uninstall lifecycle, index provisioning/teardown |

### 1.2 Endpoint

```
POST /verdity/webhooks/github
Content-Type: application/json
X-GitHub-Event: pull_request
X-Hub-Signature-256: sha256=<hex-hmac>
X-GitHub-Delivery: <uuid>
```

### 1.3 HMAC Verification (mandatory, blocking)

1. Read the raw request body **before** any JSON parsing (signature is computed over raw bytes).
2. Compute `HMAC-SHA256(webhook_secret, raw_body)`.
3. Compare against the `sha256=` value in `X-Hub-Signature-256` using a **constant-time comparison** (never `==`).
4. Reject with `401 Unauthorized` on mismatch or missing header. Do not parse or queue the payload.
5. Check `X-GitHub-Delivery` against a short-TTL dedupe cache (24h) to reject replays of a previously-processed delivery.
6. Only after (3) and (5) pass: parse JSON, normalize, publish to the Event Queue.

```python
import hmac, hashlib

def verify_signature(secret: bytes, raw_body: bytes, signature_header: str) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    provided = signature_header.split("=", 1)[1]
    return hmac.compare_digest(expected, provided)
```

### 1.4 Response Contract

- `202 Accepted` — signature valid, event queued. Body: `{"delivery_id": "...", "status": "queued"}`.
- `401 Unauthorized` — signature invalid or missing.
- `409 Conflict` — duplicate delivery ID (already processed).
- `503 Service Unavailable` — queue unreachable; GitHub will retry per its own backoff.

The gateway must respond within GitHub's timeout window (~10s) regardless of downstream load — this is the entire point of decoupling ingestion from processing.

### 1.5 Secret Rotation

Support two active secrets simultaneously (`current` + `previous`) during rotation windows; accept a signature matching either, log which one matched, and alert if `previous` is still being used after the rotation grace period expires.

## 2. Internal Event Schema (post-ingestion, normalized)

```json
{
  "event_id": "uuid",
  "delivery_id": "github-delivery-uuid",
  "trigger_type": "pr.synchronize",
  "repo": { "owner": "acme", "name": "widgets", "id": 123456 },
  "pull_request": {
    "number": 482,
    "head_sha": "a1b2c3d4",
    "base_sha": "e5f6g7h8",
    "draft": false
  },
  "received_at": "2026-08-23T10:15:00Z"
}
```

## 3. Orchestrator → Specialist Agent Contract

### 3.1 Invocation (internal, not public HTTP — shown as interface)

```json
{
  "review_run_id": "uuid",
  "specialist": "security",
  "repo": { "owner": "acme", "name": "widgets" },
  "diff_ref": { "base_sha": "e5f6g7h8", "head_sha": "a1b2c3d4" },
  "policy": { "depth": "standard", "timeout_seconds": 120, "budget_tokens": 40000 },
  "tools_enabled": ["semantic_search", "cve_lookup", "secret_scanner"]
}
```

### 3.2 Specialist Response — Findings Schema (schema-validated, required)

```json
{
  "review_run_id": "uuid",
  "specialist": "security",
  "status": "complete",
  "findings": [
    {
      "finding_id": "uuid",
      "concern": "security",
      "severity": "high",
      "file": "src/auth/session.py",
      "line_start": 42,
      "line_end": 47,
      "summary": "Session token comparison is not constant-time.",
      "explanation": "Using == to compare secrets is vulnerable to timing attacks...",
      "suggested_fix_diff": "- if token == expected:\n+ if hmac.compare_digest(token, expected):",
      "confidence": 0.91,
      "evidence": [
        { "tool": "semantic_search", "query": "session token comparison" },
        { "tool": "cve_lookup", "result": "CWE-208" }
      ],
      "agent_version": "security-agent@1.4.0",
      "prompt_hash": "sha256:..."
    }
  ],
  "tokens_used": { "input": 18234, "output": 1120 },
  "cost_usd": 0.14
}
```

`status` may be `complete`, `partial` (timed out, some findings returned), or `failed` (no findings, error recorded) — the orchestrator must handle all three without blocking other specialists.

## 4. Aggregator → Confidence Router Contract

```json
{
  "review_run_id": "uuid",
  "pr": { "owner": "acme", "name": "widgets", "number": 482 },
  "ranked_findings": [ "... deduped, severity+confidence sorted findings ..." ],
  "summary_comment_markdown": "## Review Summary\n3 high-severity issues found..."
}
```

Router splits `ranked_findings` by `confidence >= threshold`:
- `>= threshold` → **GitHub Post API** (below)
- `< threshold` → **Approval Queue API** (below)

## 5. GitHub Posting API (outbound)

Uses GitHub's REST/GraphQL API via the installed GitHub App's installation token (short-lived, scoped to the specific repo):
- `POST /repos/{owner}/{repo}/pulls/{number}/reviews` — batched inline comments + summary as a single review, not one comment per finding (avoids notification spam).
- Rate-limit aware: respects GitHub's secondary rate limits; queues/retries with backoff on `429`.

## 6. Approval Queue API (internal)

```
GET  /internal/approval-queue?repo=acme/widgets&status=pending
POST /internal/approval-queue/{finding_id}/decision
  { "decision": "approve" | "edit" | "reject", "editor": "user@acme.com", "edited_text": "..." }
```
Decisions are written to the Audit Store and feed the calibration dataset (PRD 6, FR-11).

## 7. Token Economics API (internal, dashboard-facing)

```
GET /internal/spend?scope=repo&id=acme/widgets&window=24h
→ { "spend_usd": 42.17, "budget_usd": 100.00, "tokens": {"in": 4200000, "out": 310000} }

GET /internal/spend/stream   (WebSocket/SSE for the real-time dashboard)
```

Budget enforcement: when `spend_usd` crosses configurable warn/hard thresholds, the orchestrator receives a degrade/halt signal before starting new specialist runs for that scope.

## 8. Verifier / Regression APIs (internal, coding-agent path)

```
POST /internal/verify-gate     { "proposed_diff": "...", "spec": {...} }        → pass/fail + reasons
POST /internal/verify-subagent { "proposed_diff": "...", "requirement": "..." } → independent pass/fail + notes
POST /internal/regression-run  { "proposed_diff": "...", "test_scope": "affected" | "full" } → test results
```

## 9. Error Handling & Retries

- All internal service calls are idempotent (keyed by `review_run_id` + specialist/step) so retries never duplicate findings or posted comments.
- Specialist timeout → orchestrator proceeds with partial results and flags the missing specialist in the summary comment rather than blocking the whole review.
- GitHub API failures on posting → retried with exponential backoff; after max retries, routed to Approval Queue as a fallback so findings aren't silently lost.
