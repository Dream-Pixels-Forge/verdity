# Phase 5 Gate Validation — Confidence Router + Approval Queue

**Date:** 2026-08-23
**Status:** ✅ PASSED

## Gate Criteria (from GOAL.md Section 4)

> a high-confidence critical finding gets routed to the approval queue, and a low-confidence info-level finding gets auto-dismissed.

## Results

| Check | Result | Evidence |
|-------|--------|----------|
| Critical finding routes to AUTO_APPROVE | ✅ | `test_auto_approve_critical_with_high_confidence` |
| High-confidence finding routes to AUTO_APPROVE | ✅ | `test_auto_approve_threshold` |
| Medium-confidence finding routes to MANUAL_REVIEW | ✅ | `test_manual_review_range`, `test_threshold_boundary` |
| Low-confidence finding routes to AUTO_DISMISS | ✅ | `test_auto_dismiss_below_threshold` |
| Batch routing produces mixed decisions | ✅ | `test_batches_produce_decisions` |
| Confidence score clamped to [0, 1] | ✅ | `test_score_clamped_to_unit_interval` |
| Critical score > 0.85 | ✅ | `test_critical_score_maxes_out` |
| Info severity has low score | ✅ | `test_low_severity_info_finding_has_low_score` |
| Enqueue/find pending works | ✅ | `test_enqueue_and_retrieve` |
| Resolve updates status | ✅ | `test_resolve_marked_approved` |
| Stats aggregation correct | ✅ | `test_stats_across_statuses` |

## Non-Negotiable Constraints

| # | Constraint | Status |
|---|-----------|--------|
| 5 | Deterministic confidence | ✅ | Multi-signal formula: `base × severity_weight + concern_boost`, never LLM |
| 9 | Every decision logged | ✅ | Router decisions logged via logger; approval queue records all operations |

## Files Built

```
src/verdity/
├── router.py              # Confidence scoring + routing logic (3 thresholds)
└── approval_queue.py       # SQLite-backed approval queue store

tests/
└── test_phase5.py          # 11 tests
```

## Scoring Formula

```
confidence_score = min(1.0, max(0.0, base_confidence × severity_weight + concern_boost))

severity_weights: CRITICAL=1.0, HIGH=0.8, MEDIUM=0.5, LOW=0.3, INFO=0.1
concern_boost:    SECURITY=+0.15, TESTING=+0.05, others=0
```

## Routing Thresholds

| Score | Action | Description |
|-------|--------|-------------|
| ≥ 0.90 | AUTO_APPROVE | Requires immediate action |
| ≥ 0.60 | MANUAL_REVIEW | Needs human judgment |
| < 0.60 | AUTO_DISMISS | Too low confidence to act on |

## Next: Phase 6 — Coding Agent Path
