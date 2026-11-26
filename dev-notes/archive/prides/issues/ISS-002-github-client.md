# ISS-002: GitHub Client Error Path Coverage (93% → 100%)

## Metadata
- **ID**: ISS-002
- **Phase**: I (Implement)
- **Severity**: HIGH
- **Status**: OPEN
- **Created**: 2026-10-03
- **Assigned**: implement-coder
- **Blocking**: true

## Description
GitHub client in `src/verdity/github_client.py` has 93% coverage. Missing 15 lines in error handling paths (lines 579-606).

## Missing Coverage Details

### get_pr_diff() Error Paths (lines 579-606)
- PR not found (404) - not tested
- Files API failure - not tested
- Empty diff response - not tested
- Large PR tier selection logic - not tested
- Authentication failure (401) - not tested
- Rate limiting (403) - not tested

### apply_fix() Error Paths
- Invalid branch name - not tested
- File doesn't exist - not tested
- Push rejected (non-fast-forward) - not tested
- GitHub API rate limiting (403) - not tested
- Authentication failures (401) - not tested

## Acceptance Criteria
- [ ] All 15 missing lines covered
- [ ] `pytest --cov=src/verdity/github_client --cov-fail-under=100` passes
- [ ] Error handling returns appropriate error types

## Related Files
- `src/verdity/github_client.py`
- `tests/test_github_client.py`

## Dependencies
- Requires GitHub API mocking fixtures (shared test infrastructure)

## Estimated Effort
- 5-6 new test cases
- ~4 hours
