# ISS-005: Worker, Gateway, Platforms Coverage (86-99% → 100%)

## Metadata
- **ID**: ISS-005
- **Phase**: I (Implement)
- **Severity**: LOW
- **Status**: OPEN
- **Created**: 2026-10-03
- **Assigned**: implement-coder
- **Blocking**: false

## Description
Worker, Gateway, and Platform modules have 86-99% coverage. Missing 20 lines across edge cases.

## Missing Coverage Details

### src/verdity/worker.py (86% - 18 missing lines)
- Line 79: SLA escalation loop error handling
- Lines 89-97: Backoff expiry calculation edge cases
- Lines 108-110: Shutdown task cancellation handling
- Lines 180-184: Backoff logic edge cases

### src/verdity/gateway/app.py (99% - 1 missing line)
- Line 112: Health check endpoint

### src/verdity/platforms/bitbucket.py (98% - 2 lines)
- Lines 165-166: Signature verification edge case

### src/verdity/platforms/gitlab.py (98% - 2 lines)
- Lines 155-156: Signature verification edge case

### src/verdity/approval_queue.py (51% per GOAL - 41 lines)
- Lines 103-135: `enqueue()` method
- Lines 139-173: `get_pending()`, `get_item()`, `resolve()`
- Lines 178-192: SLA escalation
- Lines 197-202: Stats
- Lines 206-216: Repo filtering
- Lines 219-229: Cleanup
- Lines 238-270: Partitioning logic

## Acceptance Criteria
- [ ] All missing lines covered
- [ ] `pytest --cov=src/verdity/worker --cov-fail-under=100` passes
- [ ] `pytest --cov=src/verdity/gateway --cov-fail-under=100` passes
- [ ] `pytest --cov=src/verdity/platforms --cov-fail-under=100` passes
- [ ] `pytest --cov=src/verdity/approval_queue --cov-fail-under=100` passes

## Related Files
- `src/verdity/worker.py`
- `src/verdity/gateway/app.py`
- `src/verdity/platforms/bitbucket.py`
- `src/verdity/platforms/gitlab.py`
- `src/verdity/approval_queue.py`
- `tests/test_worker.py`
- `tests/test_approval_queue.py` (new)

## Dependencies
- Requires time manipulation for SLA tests (freezegun)
- Approval queue needs dedicated test file

## Estimated Effort
- 15-20 new test cases
- ~10 hours
