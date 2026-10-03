# ISS-003: MCP Server Tool Coverage (70% → 100%)

## Metadata
- **ID**: ISS-003
- **Phase**: I (Implement)
- **Severity**: HIGH
- **Status**: OPEN
- **Created**: 2026-10-03
- **Assigned**: implement-coder
- **Blocking**: true

## Description
MCP Server in `src/verdity/mcp_server.py` has 70% coverage. Missing 85 lines across tool handlers.

## Missing Coverage Details

### Tool Definitions (lines 51-70)
- Tool definitions not exercised in tests
- Need integration tests for all 12 tools

### Specialist Review Tools Error Paths (lines 453, 455, 457, 459)
- `_review_testing` error paths
- `_review_documentation` error paths
- `_review_full` error paths
- Agent initialization failures not tested

### verdity_review Tool (lines 694-791) - CRITICAL
- PR fetching via `get_pr_diff()` - not tested
- Diff conversion to diff_files format - not tested
- GitHub posting (check run creation) - not tested
- Tier selection logic - not tested
- PR not found (404) - not tested
- Empty diff - not tested
- Large PR tier selection - not tested

### verdity_enforce Tool (lines 795-844) - CRITICAL
- Rule loading from custom file - not tested
- Fallback to `.verdity/rules.yml` - not tested
- Finding proxy creation - not tested
- Various finding types - not tested

### verdity_rules_list Tool (lines 848-867)
- File not found error - not tested
- YAML parse errors - not tested
- Missing rules key - not tested

### verdity_review_status Tool (lines 873-875) - NOT IMPLEMENTED
- **NOT IMPLEMENTED** - returns placeholder
- Need to either implement or mark deprecated

### verdity_rules_list Tool (lines 848-867)
- File not found error - not tested
- YAML parse errors - not tested

## Acceptance Criteria
- [ ] All 85 missing lines covered
- [ ] `pytest --cov=src/verdity/mcp_server --cov-fail-under=100` passes
- [ ] `verdity_review_status` implemented or deprecated
- [ ] All 12 tools have integration tests

## Related Files
- `src/verdity/mcp_server.py`
- `tests/test_mcp_server.py`

## Dependencies
- Requires GitHub API mocking fixtures
- Requires MCP server integration test helper

## Estimated Effort
- 15-20 new test cases + 1 implementation
- ~12 hours
