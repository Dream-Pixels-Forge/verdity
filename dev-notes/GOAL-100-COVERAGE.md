# GOAL: Achieve 100% Test Coverage for Production Readiness

## Current State: 94.07% Coverage (896 tests pass)

### Coverage Breakdown by Category

| Category | Coverage | Missing Lines | Priority |
|----------|----------|---------------|----------|
| **Core Business Logic** | 100% | 0 | ✅ DONE |
| **Enforcement Engine** | 84% | 21 lines | 🟡 HIGH |
| **GitHub Client** | 93% | 15 lines | 🟡 HIGH |
| **MCP Server** | 70% | 85 lines | 🟡 MEDIUM |
| **CLI Commands** | 30-89% | 252 lines | 🔴 MEDIUM |
| **Worker/Gateway** | 86-99% | 20 lines | 🟢 LOW |
| **Platform Specific** | 98-100% | 4 lines | 🟢 LOW |

---

## Phase 1: Enforcement Engine (84% → 100%)

### Missing Coverage: 21 lines in `src/verdity/enforcement.py`

**Lines Missing:**
- Lines 42-44: `GateRule.evaluate()` exception handling
- Line 59: `substitute_variables()` empty variables check
- Line 61: `substitute_variables()` non-string value conversion
- Lines 158, 190-194: `RuleSet.evaluate()` finding proxy attribute access
- Lines 266-285: `EnforcementEngine.evaluate_with_context()` edge cases

**Action Items:**
1. Add tests for `GateRule.evaluate()` when `context.get("finding")` returns empty dict
2. Test `substitute_variables()` with empty dict, None values, boolean values
3. Test `RuleSet.evaluate()` with findings missing optional attributes (explanation, content)
4. Test `EnforcementEngine.evaluate_with_context()` with various variable combinations

**Estimated Effort:** 4-6 new test cases

---

## Phase 2: GitHub Client (93% → 100%)

### Missing Coverage: 15 lines in `src/verdity/github_client.py`

**Lines Missing (579-606):**
- `apply_fix()` method - error handling paths
- Branch creation failures
- Commit/push error scenarios
- File not found errors

**Action Items:**
1. Add tests for `apply_fix()` with:
   - Invalid branch name
   - File doesn't exist
   - Push rejected (non-fast-forward)
   - GitHub API rate limiting (403)
   - Authentication failures (401)

**Estimated Effort:** 5-6 new test cases

---

## Phase 3: MCP Server (70% → 100%)

### Missing Coverage: 85 lines in `src/verdity/mcp_server.py`

**Key Missing Areas:**
- Lines 51-70: Tool definitions not exercised in tests
- Lines 453, 455, 457, 459: `_review_testing`, `_review_documentation`, `_review_full` error paths
- Lines 694-791: `verdity_review` tool - PR fetching, diff conversion, GitHub posting
- Lines 795-844: `verdity_enforce` - rule loading, finding proxy creation
- Lines 848-867: `verdity_rules_list` - file not found, YAML parse errors
- Lines 873-875: `verdity_review_status` - not implemented

**Action Items:**
1. Add integration tests for all MCP tools using mocked GitHub client
2. Test `verdity_review` with:
   - PR not found (404)
   - Empty diff
   - Large PR (tier selection)
   - GitHub posting success/failure
3. Test `verdity_enforce` with:
   - Custom rules file
   - Missing rules file (fallback to .verdity/rules.yml)
   - Various finding types
4. Test `verdity_rules_list` error paths
5. Implement `verdity_review_status` or mark as deprecated

**Estimated Effort:** 15-20 new test cases + 1 implementation

---

## Phase 4: CLI Commands (30-89% → 100%)

### `src/verdity/cli/review.py` (30% coverage - 89 missing lines)

**Missing:**
- Lines 22: `review` command group
- Lines 74-96: `diff` command - PR fetching, output formatting
- Lines 101-128: `run` command - server initialization, review execution
- Lines 139-167: `enforce` command - finding loading, rule loading, variable parsing
- Lines 177-221: Output formatting (JSON/text)
- Lines 226-245: Error handling
- Lines 249: Main entry point

### `src/verdity/cli/enforce.py` (89% coverage - 11 missing lines)

**Missing:**
- Lines 36-37: `load_rules()` empty rules file
- Lines 108-109: `load_finding()` invalid JSON
- Lines 120-122: `create_finding_proxy()` missing attributes
- Line 139: Variable parsing error
- Lines 159-160: Rule evaluation errors
- Line 168: Main entry point

**Action Items:**
1. Add CLI integration tests using `click.testing.CliRunner`
2. Test all commands with:
   - Valid inputs
   - Invalid/missing inputs
   - Error conditions
   - Output format variations
3. Mock GitHub client and MCP server for isolation

**Estimated Effort:** 20-25 new test cases

---

## Phase 5: Worker & Gateway (86-99% → 100%)

### `src/verdity/worker.py` (86% - 18 missing lines)
- Lines 79, 89-97, 108-110, 180-184: Error handling, shutdown paths, backoff logic

### `src/verdity/gateway/app.py` (99% - 1 missing line)
- Line 112: Health check endpoint

### `src/verdity/platforms/bitbucket.py` (98% - 2 lines)
- Lines 165-166: Signature verification edge case

### `src/verdity/platforms/gitlab.py` (98% - 2 lines)
- Lines 155-156: Signature verification edge case

### `src/verdity/approval_queue.py` (51% - 41 lines)
- Lines 103-135: `enqueue()` method
- Lines 139-173: `get_pending()`, `get_item()`, `resolve()`
- Lines 178-192: SLA escalation
- Lines 197-202: Stats
- Lines 206-216: Repo filtering
- Lines 219-229: Cleanup
- Lines 238-270: Partitioning logic

**Action Items:**
1. Add integration tests for approval queue CRUD operations
2. Test SLA escalation with time manipulation
3. Test partitioning by repo_id
4. Add gateway health check test
4. Test platform signature edge cases

**Estimated Effort:** 15-20 new test cases

---

## Phase 6: Version Module Edge Cases (69% → 100%)

### `src/verdity/_version.py` (69% - 8 missing lines)
- Lines 24-26: Fallback path logic
- Lines 30-34: Installed package fallback

**Action Items:**
1. Test fallback to `sys.prefix/share/verdity/pyproject.toml`
2. Test when neither path exists

**Estimated Effort:** 2-3 new test cases

---

## Implementation Priority Order

| Phase | Target | Effort | Impact |
|-------|--------|--------|--------|
| 1 | Enforcement Engine | Low (4-6 tests) | High - Core security feature |
| 2 | GitHub Client | Low (5-6 tests) | High - External integration |
| 6 | Version Module | Very Low (2-3 tests) | Low - Edge cases |
| 3 | MCP Server | Medium (15-20 tests) | High - Agent interface |
| 5 | Worker/Gateway/Platforms | Medium (15-20 tests) | Medium - Operations |
| 4 | CLI Commands | High (20-25 tests) | Medium - User interface |

---

## Test Infrastructure Needed

### 1. GitHub API Mocking
```python
# Create shared fixtures for GitHub client mocking
@pytest.fixture
def mock_github_client():
    with patch("verdity.github_client.GitHubClient") as mock:
        yield mock
```

### 2. MCP Server Integration Test Helper
```python
# Test utilities for MCP server
async def call_mcp_tool(server, name, args):
    return await server.call_tool(name, args)
```

### 3. CLI Test Runner
```python
from click.testing import CliRunner

runner = CliRunner()
result = runner.invoke(verdity_review, ["run", "--owner", "org", "--repo", "repo", "--pr", "1"])
```

### 4. Time Manipulation for SLA Tests
```python
import freezegun

with freezegun.freeze_time("2024-01-01 12:00:00"):
    # Test SLA escalation
```

---

## Success Criteria

- [ ] `pytest --cov=src/verdity --cov-fail-under=100` passes
- [ ] All 900+ tests pass
- [ ] No uncovered lines in business logic
- [ ] CI/CD pipeline enforces 100% on merge
- [ ] Coverage badge shows 100%

---

## Estimated Total Effort

| Phase | Tests to Add | Hours |
|-------|--------------|-------|
| 1 | 6 | 4 |
| 2 | 6 | 4 |
| 6 | 3 | 2 |
| 3 | 18 | 12 |
| 5 | 18 | 10 |
| 4 | 23 | 16 |
| **Total** | **74** | **48** |

---

## Notes

1. **Focus on business logic first** - Core enforcement, GitHub integration, MCP tools
2. **CLI tests can be integration-style** - Mock external dependencies, test command flows
3. **Approval queue needs most work** - Only 51% covered, critical for enforcement feature
4. **Consider test organization** - Separate unit vs integration tests for faster CI
5. **Maintain 100%** - Add `--cov-fail-under=100` to CI pipeline permanently
