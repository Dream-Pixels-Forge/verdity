# ISS-007: Approval Queue Comprehensive Coverage (51% → 100%)

## Metadata
- **ID**: ISS-007
- **Phase**: I (Implement)
- **Severity**: HIGH
- **Status**: OPEN
- **Created**: 2026-10-03
- **Assigned**: implement-coder
- **Blocking**: true

## Description
Approval queue in `src/verdity/approval_queue.py` has only 51% coverage per GOAL-100-COVERAGE.md. Critical for enforcement feature.

## Missing Coverage Details (41 lines)

### enqueue() method (lines 103-135)
- Adding items to queue
- Repo partitioning logic
- SLA hours configuration

### get_pending(), get_item(), resolve() (lines 139-173)
- Pending items retrieval with repo filtering
- Item retrieval by ID
- Item resolution (approve/reject)

### SLA Escalation (lines 178-192)
- SLA hours tracking
- Escalation logic with time manipulation
- Escalated items retrieval

### Stats (lines 197-202)
- Queue statistics per repo
- Escalation counts

### Repo Filtering (lines 206-216)
- Per-repo queue isolation
- Cross-repo queries

### Cleanup (lines 219-229)
- Expired item removal
- Completed item archival

### Partitioning Logic (lines 238-270)
- Repo-based table partitioning
- Schema management per partition

## Acceptance Criteria
- [ ] All 41 missing lines covered
- [ ] `pytest --cov=src/verdity/approval_queue --cov-fail-under=100` passes
- [ ] SLA escalation tested with time manipulation (freezegun)
- [ ] Repo partitioning tested

## Related Files
- `src/verdity/approval_queue.py`
- `tests/test_approval_queue.py` (new - needs creation)

## Dependencies
- Requires `freezegun` for time manipulation
- Needs dedicated test file (doesn't exist yet)
- Requires `AsyncConnection` mocking

## Estimated Effort
- 15-20 new test cases
- ~10 hours
