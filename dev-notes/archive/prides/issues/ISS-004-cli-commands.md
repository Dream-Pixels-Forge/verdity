# ISS-004: CLI Commands Coverage (30-89% → 100%)

## Metadata
- **ID**: ISS-004
- **Phase**: I (Implement)
- **Severity**: MEDIUM
- **Status**: OPEN
- **Created**: 2026-10-03
- **Assigned**: implement-coder
- **Blocking**: false

## Description
CLI commands have very low coverage (30-89%). Need integration tests for all commands.

## Missing Coverage Details

### src/verdity/cli/review.py (30% - 89 missing lines)
- Lines 22: `review` command group
- Lines 74-96: `diff` command - PR fetching, output formatting
- Lines 101-128: `run` command - server initialization, review execution
- Lines 139-167: `enforce` command - finding loading, rule loading, variable parsing
- Lines 177-221: Output formatting (JSON/text)
- Lines 226-245: Error handling
- Line 249: Main entry point

### src/verdity/cli/enforce.py (89% - 11 missing lines)
- Lines 36-37: `load_rules()` empty rules file
- Lines 108-109: `load_finding()` invalid JSON
- Lines 120-122: `create_finding_proxy()` missing attributes
- Line 139: Variable parsing error
- Lines 159-160: Rule evaluation errors
- Line 168: Main entry point

### Duplicate Finding Proxy Implementation
- `src/verdity/mcp_server.py` (lines 49-70): `_create_finding_proxy()`
- `src/verdity/cli/review.py` (lines 224-245): `_create_finding_proxy()`
- `src/verdity/cli/enforce.py` (lines 58-79): `create_finding_proxy()`
- **Should centralize in `enforcement.py` or shared utility**

## Acceptance Criteria
- [ ] All missing lines covered
- [ ] `pytest --cov=src/verdity/cli --cov-fail-under=100` passes
- [ ] All commands tested with valid/invalid inputs
- [ ] Finding proxy centralized

## Related Files
- `src/verdity/cli/review.py`
- `src/verdity/cli/enforce.py`
- `tests/test_cli_review.py` (new)
- `tests/test_cli_enforce.py` (new)

## Dependencies
- Requires CLI test runner (`click.testing.CliRunner`)
- Requires mocked GitHub client and MCP server

## Estimated Effort
- 20-25 new test cases
- ~16 hours
