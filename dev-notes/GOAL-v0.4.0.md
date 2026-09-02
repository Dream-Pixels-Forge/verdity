# GOAL.md — v0.4.0

## Build Charter — Verdity v0.4.0 "Intelligence"

**Read this file first, and re-read it before starting any new phase or after any context reset.**
This file is the single source of truth for scope and sequencing. If anything in a conversation, a passing idea, or an intermediate step conflicts with this file, **this file wins**.

**Predecessor:** v0.3.1 (409 tests, 0 ruff errors, all 8 phases complete)
**Theme:** Engineering analytics, trust calibration, adversarial self-review, LLM agent integration, multi-platform support

---

## 0. Reference Documents (authoritative — do not contradict these)

inside `verdity/dev-notes` folder:

| Doc                                     | Answers                                                                               |
| --------------------------------------- | ------------------------------------------------------------------------------------- |
| `GOAL.md` (v0.3.x)                     | v0.3.x build charter — non-negotiable constraints still apply                        |
| `01-Product-Requirements-Document.md`   | What are we building and why? What's in/out of scope? What defines success?           |
| `02-Technical-Architecture-Document.md` | What are the components, how do they connect, what's the tech stack?                  |
| `03-API-Webhook-Specification.md`       | What are the exact request/response contracts, schemas, and endpoints?                |
| `04-Security-Threat-Model.md`           | What must never be violated (HMAC, least privilege, tenant isolation, secrets)?       |
| `05-Agent-Orchestration-Design.md`      | How do agents coordinate, how is confidence computed, how do verification gates work? |
| `RESEARCH-2026-AI-CODE-REVIEW.md`       | 2026 market landscape, competitor analysis, feature recommendations                   |
| `competitive_analysis_state.json`       | Verdity's market position, gaps, and strengths                                        |

**Rule: before writing any component, locate it in these docs or in this file. If it isn't there, stop and flag it rather than inventing it.** If a genuinely new requirement emerges mid-build, update the relevant doc first, then build.

---

## 1. One-Sentence Mission

Make Verdity **intelligent** — it learns from human feedback, catches its own false positives, exposes engineering ROI metrics, optionally uses LLMs for deeper analysis, and works on GitLab/Bitbucket in addition to GitHub.

---

## 2. Non-Negotiable Constraints (from v0.3.x GOAL.md §2 — still binding)

1. **Every** inbound webhook is HMAC-SHA256 verified over the raw body with constant-time comparison before any parsing.
2. Ingestion and processing are **decoupled** via a durable queue.
3. Specialist agents run **in parallel**, not sequentially.
4. There is **one** semantic index/data store serving all specialists.
5. Confidence scores are computed by **deterministic post-processing code**, never by asking the LLM "how confident are you."
6. Code changes pass: verification gate → independent verifier → regression run, in that order.
7. Findings below the confidence threshold **never** auto-post — they go to the Approval Queue.
8. Every model call is metered by the Token Economics Service.
9. Every finding and every approval-queue decision is written to the append-only Audit Store.

### Additional constraints for v0.4.0

10. **LLM calls are optional.** Every agent must produce valid findings WITHOUT an LLM (deterministic regex fallback). LLM is an enhancement, not a requirement.
11. **Trust calibration adjusts weights, not individual scores.** The `TrustCalibrator` modifies `SEVERITY_WEIGHTS` and `CONCERN_BOOST` maps — it never rewrites a specific finding's confidence.
12. **Adversarial review uses a separate prompt/context.** It must not share the same system prompt as the initial agent (prevents self-confirmation bias, same principle as VerificationGate → VerifierSubagent).
13. **Multi-platform webhook verification uses platform-native mechanisms.** GitLab uses `X-Gitlab-Token`, Bitbucket uses HMAC-SHA256. Do not force GitHub's `X-Hub-Signature-256` on other platforms.
14. **Metrics are append-only.** No UPDATE or DELETE on `review_metrics`, `finding_outcomes`, or `review_timings` tables.

---

## 3. Definition of Done for v0.4.0

The project is **not done** at "the features exist." It is done when all of the following are true:

- [ ] `GET /metrics/{repo_id}` returns review volume, severity trends, false positive rates, time-to-resolution, and cost-per-review — all populated with real data from at least one review cycle.
- [ ] After 50+ human decisions recorded, `TrustCalibrator.recalibrate()` produces adjusted weights where precision@0.9 > 0.8 (80% of auto-approved findings are confirmed).
- [ ] Adversarial self-review overturns >30% of false-positive findings in a test set of 10 findings (5 true positive, 5 false positive).
- [ ] At least one specialist agent (e.g. security) produces higher-quality findings with `use_llm=True` than with `use_llm=False` on the same diff.
- [ ] GitLab and Bitbucket webhooks are normalized to the same internal `WebhookEvent` schema as GitHub.
- [ ] All 409 existing tests still pass. New tests bring total to ~470. Coverage remains 100%.
- [ ] 0 ruff errors.
- [ ] Package builds, `twine check dist/*` passes.

None of these are "nice to have later." A build that skips any of them is incomplete.

---

## 4. Build Order (do not reorder without a reason recorded in this file)

Build in this sequence. Each phase has a gate — **do not start the next phase until the current phase's gate passes.**

Phases 9–13 are **independent** — they can be built in any order or in parallel. Phase 14 depends on all of them.

### Phase 9 — Engineering Analytics Dashboard

**Why:** No competitor tracks engineering ROI from code review. Optibot differentiates with metrics. This closes Verdity's biggest market gap.

**Build:**
- New file: `src/verdity/metrics_store.py` — append-only metrics store with 3 tables:
  - `review_metrics` (repo_id, pr_number, metric_type, metric_key, metric_value)
  - `finding_outcomes` (finding_id, final_outcome: confirmed/false_positive/wont_fix/auto_fixed)
  - `review_timings` (repo_id, pr_number, phase, duration_ms)
- Edit: `src/verdity/orchestrator.py` — after each review run, call `metrics_store.record_review_metrics()` with finding counts, severity distribution, agent costs, and timing per phase.
- Edit: `src/verdity/router.py` — when a finding is auto-approved or dismissed, call `metrics_store.record_finding_outcome()`.
- New endpoints in `src/verdity/gateway/app.py`:
  - `GET /metrics/{repo_id}?days=30` — returns summary metrics
  - `GET /metrics/{repo_id}/dashboard?days=30` — returns chart-ready data structure
- New file: `tests/test_metrics_store.py`

**Gate test:** `test_gate_phase9_metrics` — a full review cycle writes metrics, and `get_repo_summary()` returns valid data with all required fields (review_count, severity_distribution, false_positive_rate, median_time_to_review, cost_per_review).

**Run gate:**
```bash
pytest tests/test_metrics_store.py::test_gate_phase9_metrics -v
pytest tests/ -x -q
ruff check src/
```

---

### Phase 10 — Trust Calibration

**Why:** Confidence scores should improve over time based on human feedback. Currently weights are static.

**Build:**
- New file: `src/verdity/trust_calibration.py` — `TrustCalibrator` class:
  - `record_outcome(finding_type, outcome, repo_id, confidence, severity, concern)` — store human decisions
  - `recalibrate(min_samples=50)` — compute adjusted `SEVERITY_WEIGHTS` and `CONCERN_BOOST` from outcome history
  - `get_adjusted_weights()` — return calibrated weights or defaults
  - `get_calibration_stats()` — return precision@0.9, recall@0.6, sample_count
- Edit: `src/verdity/router.py` — `compute_confidence()` accepts optional `severity_weights` and `concern_boost` parameters; uses calibrated weights when available, defaults when not.
- New file: `tests/test_trust_calibration.py`

**Gate test:** `test_gate_phase10_trust` — record 60 outcomes (40 confirmed, 20 false_positive), recalibrate, verify precision@0.9 > 0.8.

**Run gate:**
```bash
pytest tests/test_trust_calibration.py::test_gate_phase10_trust -v
pytest tests/ -x -q
ruff check src/
```

---

### Phase 11 — Self-Review Adversarial Loop

**Why:** SonarQube Hunter Agent achieves 80-90% precision via adversarial verification. Verdity currently has no false-positive reduction mechanism.

**Build:**
- New file: `src/verdity/adversarial_reviewer.py` — `AdversarialReviewer` class:
  - `challenge_findings(findings, diff, file_contents)` — for each finding, attempt to DISPROVE it
  - Returns `AdversarialResult(finding_id, verdict: confirmed/disputed/overturned, reasoning, suggested_confidence_adjustment, evidence)`
  - `_apply_verdicts(findings, verdicts)` — apply verdicts: confirmed→keep+boost, disputed→flag for manual review, overturned→remove
- Edit: `src/verdity/orchestrator.py` — after aggregation, before routing, run adversarial review if `policy.adversarial_review_enabled`
- Edit: `src/verdity/schemas/_models.py` — add `adversarial_review_enabled: bool = True` and `adversarial_review_depth: Literal["lite", "full"] = "lite"` to `ReviewPolicy`
- New file: `tests/test_adversarial_reviewer.py`

**Gate test:** `test_gate_phase11_adversarial` — create 10 findings (5 true positive, 5 false positive), run adversarial review, verify >3 of the 5 false positives are overturned.

**Run gate:**
```bash
pytest tests/test_adversarial_reviewer.py::test_gate_phase11_adversarial -v
pytest tests/ -x -q
ruff check src/
```

---

### Phase 12 — LLM Agent Integration

**Why:** Deterministic regex catches patterns. LLMs catch logic issues, context-dependent bugs, and nuance. 2026 leaders (Copilot, Greptile, SonarQube Hunter) all use LLMs for deeper analysis.

**Build:**
- New file: `src/verdity/llm_client.py` — `LLMClient` class:
  - `complete(model, messages, temperature=0.0, max_tokens=4096)` — send completion, return `LLMResponse(content, input_tokens, output_tokens, model, cost_usd)`
  - `complete_structured(model, messages, schema, temperature=0.0, max_retries=2)` — parse output into Pydantic model, retry on schema mismatch
  - All calls go through `TokenEconomicsService.record_call()` (constraint #8)
- Edit: `src/verdity/agents/security.py` — add `use_llm: bool = False` parameter to `review()`. When True, run LLM-enhanced scan after deterministic regex.
- Edit: `src/verdity/agents/code_quality.py` — same pattern.
- Edit: `src/verdity/agents/testing.py` — same pattern.
- Edit: `src/verdity/agents/documentation.py` — same pattern.
- Edit: `src/verdity/config.py` — add LLM settings:
  - `llm_enabled: bool = False`
  - `llm_model: str = "gpt-4o-mini"`
  - `llm_security_model: str = "gpt-4o"`
  - `llm_temperature: float = 0.0`
  - `llm_max_tokens: int = 4096`
- New file: `tests/test_llm_client.py`
- New file: `tests/test_agents_llm.py`

**Gate test:** `test_gate_phase12_llm` — security agent with `use_llm=True` finds at least one issue that regex-only misses on a test diff containing a logic flaw.

**Run gate:**
```bash
pytest tests/test_llm_client.py::test_gate_phase12_llm -v
pytest tests/ -x -q
ruff check src/
```

---

### Phase 13 — Multi-Platform Webhook Support

**Why:** CodeRabbit supports GitHub/GitLab/Bitbucket/Azure. Verdity is GitHub-only. This is the #1 enterprise gap.

**Build:**
- New directory: `src/verdity/platforms/`
- New file: `src/verdity/platforms/__init__.py` — exports `Platform`, `GitHubPlatform`, `GitLabPlatform`, `BitbucketPlatform`
- New file: `src/verdity/platforms/base.py` — abstract `Platform` class:
  - `verify_webhook(headers, body, secret)` — platform-native verification
  - `normalize_event(headers, body)` — convert to `WebhookEvent`
  - `post_comment(owner, repo, pr_number, body)` — post review comment
  - `post_inline_comment(owner, repo, pr_number, commit_sha, file_path, line, body)` — post inline comment
- New file: `src/verdity/platforms/github.py` — refactor existing `github_client.py` logic into this class. Keep `github_client.py` as thin wrapper for backward compatibility.
- New file: `src/verdity/platforms/gitlab.py` — GitLab webhook verification (`X-Gitlab-Token`) + MR event normalization
- New file: `src/verdity/platforms/bitbucket.py` — Bitbucket webhook verification (HMAC-SHA256) + PR event normalization
- Edit: `src/verdity/gateway/app.py` — add `POST /webhook/{platform}` unified endpoint. Keep existing `/webhook` for backward compatibility.
- Edit: `src/verdity/config.py` — add platform settings:
  - `default_platform: str = "github"`
  - `gitlab_webhook_secret: SecretStr = ""`
  - `bitbucket_webhook_secret: SecretStr = ""`
- New file: `tests/test_platforms_gitlab.py`
- New file: `tests/test_platforms_bitbucket.py`

**Gate test:** `test_gate_phase13_platforms` — GitLab MR opened webhook → verified → normalized to `TriggerType.PR_OPENED` → queued → visible on queue. Same for Bitbucket PR opened.

**Run gate:**
```bash
pytest tests/test_platforms_gitlab.py::test_gate_phase13_platforms -v
pytest tests/test_platforms_bitbucket.py::test_gate_phase13_platforms -v
pytest tests/ -x -q
ruff check src/
```

---

### Phase 14 — Changelog, Version Bump, Release Prep

**Build:**
- Update `CHANGELOG.md` with all v0.4.0 additions
- Update `pyproject.toml` version to `0.4.0`
- Update `competitive_analysis_state.json` — remove gaps now closed
- Run full test suite, ruff, build, twine check

**Gate:**
```bash
pytest tests/ -x -q
ruff check src/
python -m build
twine check dist/*
```

---

## 5. Anti-Drift Rules for the Building Agent

Inherited from v0.3.x GOAL.md §5, plus:

- **Don't rename components mid-build.** If the docs say `MetricsStore`, the code says `MetricsStore`, not `AnalyticsDB` or `StatsService`.
- **Don't add a specialist, a data store, or a new external dependency that isn't in this file or the Technical Architecture Document** without first adding it there and stating why.
- **Don't bypass the adversarial review prompt separation.** The adversarial reviewer MUST use a different system prompt than the initial agents. This is a safety property, not an implementation detail.
- **Don't let LLM become required.** Every agent must produce valid findings with `use_llm=False`. If you find yourself writing code that only works with an LLM, stop — the deterministic path is the primary path.
- **Don't adjust individual finding confidence in TrustCalibrator.** It adjusts weight maps, not per-finding scores. This preserves determinism (constraint #5).
- **Don't force GitHub webhook verification on GitLab/Bitbucket.** Each platform uses its own verification mechanism.
- **Don't add metrics endpoints that leak secrets.** The `/metrics` endpoints must not expose API keys, tokens, or HMAC secrets.
- **If a requirement is ambiguous, re-check the PRD and Architecture docs before guessing.** If still ambiguous, make the smallest reasonable assumption, log it in §7 Assumptions Log, and proceed.
- **At the end of every phase, re-read Section 2 (Non-Negotiable Constraints) before marking the phase done.** This is the fastest check against silent scope drift.

---

## 6. What "Done" Is Not

- Not done: a metrics endpoint that returns zero values because nothing is recorded.
- Not done: trust calibration that "works" but hasn't been tested with 50+ real outcomes.
- Not done: adversarial review that challenges findings but never overturns any (no false-positive reduction).
- Not done: LLM integration that only works with a specific API key and crashes without it.
- Not done: GitLab webhook normalization that only handles MR events but not push or comment events.
- Not done: metrics that show data but can't prove the data is accurate (no validation against audit store).
- Not done: confidence scores that change based on LLM output (violates constraint #5).

---

## 7. Assumptions Log

Inherited from v0.3.x GOAL.md §7, plus:

| # | Date | Phase | Assumption | Why |
|---|------|-------|-----------|-----|
| 10 | 2026-09-02 | 9 | MetricsStore uses SQLite for dev (swappable to Timescale/Prometheus in prod) | Same pattern as AuditStore and EventQueue. Architecture doc §2.2 permits either. |
| 11 | 2026-09-02 | 10 | Trust calibration runs async (not blocking review flow) | Recalibration is a background task; reviews use current weights until calibration completes. |
| 12 | 2026-09-02 | 11 | Adversarial review runs on all findings (not just high-confidence ones) | False positives can appear at any confidence level. Limiting to high-confidence would miss the most impactful corrections. |
| 13 | 2026-09-02 | 12 | LLM integration uses existing `model_fallback.py` for retry/fallback | No need to build a second fallback mechanism. `MultiModelFallback` already handles cooldown and exponential backoff. |
| 14 | 2026-09-02 | 13 | GitLab and Bitbucket use the same `WebhookEvent` schema as GitHub | Internal schema must be platform-agnostic. Platform-specific fields go in `raw_headers`/`raw_body`. |
| 15 | 2026-09-02 | 12 | LLM temperature=0.0 by default for determinism | Constraint #5 requires deterministic scoring. LLM output varies with temperature; 0.0 minimizes variance. |
| 16 | 2026-09-02 | 11 | Adversarial review uses "lite" depth by default | Full adversarial review is expensive. Lite mode challenges findings with a focused prompt; full mode can be enabled per-repo. |

---

## 8. Task Dependency Graph

```
Phase 9  (Metrics)         ──────────────────────────────────────────┐
Phase 10 (Trust Calibration) ── depends on Phase 9 (reads MetricsStore) ──┤
Phase 11 (Adversarial Review) ──────────────────────────────────────┤
Phase 12 (LLM Integration)   ──────────────────────────────────────┼──▶ Phase 14 (Release)
Phase 13 (Multi-Platform)    ──────────────────────────────────────┘
```

Phases 9, 11, 12, 13 are fully independent. Phase 10 depends on Phase 9 (it reads from `MetricsStore` to get outcome history). Phase 14 depends on all.

---

## 9. File Manifest

| Phase | New Files | Edited Files |
|-------|-----------|--------------|
| 9 | `metrics_store.py`, `test_metrics_store.py` | `orchestrator.py`, `router.py`, `gateway/app.py` |
| 10 | `trust_calibration.py`, `test_trust_calibration.py` | `router.py` |
| 11 | `adversarial_reviewer.py`, `test_adversarial_reviewer.py` | `orchestrator.py`, `schemas/_models.py` |
| 12 | `llm_client.py`, `test_llm_client.py`, `test_agents_llm.py` | `agents/security.py`, `agents/code_quality.py`, `agents/testing.py`, `agents/documentation.py`, `config.py` |
| 13 | `platforms/__init__.py`, `platforms/base.py`, `platforms/github.py`, `platforms/gitlab.py`, `platforms/bitbucket.py`, `test_platforms_gitlab.py`, `test_platforms_bitbucket.py` | `gateway/app.py`, `config.py` |
| 14 | — | `CHANGELOG.md`, `pyproject.toml`, `competitive_analysis_state.json` |
