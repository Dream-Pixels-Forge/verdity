# ISS-006: Version Module Fallback Coverage (69% → 100%)

## Metadata
- **ID**: ISS-006
- **Phase**: I (Implement)
- **Severity**: LOW
- **Status**: OPEN
- **Created**: 2026-10-03
- **Assigned**: implement-coder
- **Blocking**: false

## Description
Version module in `src/verdity/_version.py` has 69% coverage. Missing 8 lines in fallback logic.

## Missing Coverage Details

### Fallback Path Logic (lines 24-26)
- Fallback to `sys.prefix/share/verdity/pyproject.toml` not tested
- Behavior when project root doesn't have pyproject.toml
- Need to test when fallback path exists vs doesn't exist

### Installed Package Fallback (lines 30-34)
- Behavior when neither path exists
- Returns "0.0.0+unknown" - not tested
- Import fallback for tomllib/tomli - partially tested

## Acceptance Criteria
- [ ] All 8 missing lines covered
- [ ] `pytest --cov=src/verdity/_version --cov-fail-under=100` passes
- [ ] Fallback path logic tested

## Related Files
- `src/verdity/_version.py`
- `tests/test_version.py`

## Dependencies
- Requires mocking `sys.prefix` and filesystem paths

## Estimated Effort
- 2-3 new test cases
- ~2 hours
