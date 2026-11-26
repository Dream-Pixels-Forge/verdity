# Task Plan: 100% Test Coverage for Verdity

## Overview
Current state: 94.07% coverage (896 tests pass)
Target: 100% coverage with all 900+ tests passing

## Phase 1: Enforcement Engine (84% → 100%) - HIGH PRIORITY, BLOCKING ✅ COMPLETED
**File:** `src/verdity/enforcement.py` (21 missing lines)
**Estimated:** 4-6 new test cases, 4 hours
**Actual:** 14 new test cases added

### Missing Lines Covered:
- ✅ Lines 42-44: `GateRule.evaluate()` exception handling - Tests: `test_gate_rule_evaluate_empty_finding_dict`, `test_gate_rule_evaluate_exception_handling`, `test_gate_rule_evaluate_finding_none`
- ✅ Line 59: `substitute_variables()` empty variables check - Tests: `test_substitute_variables_empty_dict`, `test_substitute_variables_none_variables`
- ✅ Line 61: `substitute_variables()` non-string value conversion - Tests: `test_substitute_variables_boolean_values`, `test_substitute_variables_none_value`, `test_substitute_variables_numeric_values`
- ✅ Lines 158, 190-194: `RuleSet.evaluate()` finding proxy attribute access - Tests: `test_rule_set_evaluate_missing_optional_attributes`, `test_rule_set_evaluate_disabled_rule_skipped`
- ✅ Lines 266-285: `EnforcementEngine.evaluate_with_context()` edge cases - Tests: `test_evaluate_with_context_empty_variables`, `test_evaluate_with_context_none_variables`, `test_evaluate_with_context_multiple_variables`, `test_evaluate_with_context_no_matching_rules`, `test_evaluate_with_context_variable_in_when_and_message`

### Test Cases Added (14 total):
1. `TestGateRuleEdgeCases.test_gate_rule_evaluate_empty_finding_dict`
2. `TestGateRuleEdgeCases.test_gate_rule_evaluate_exception_handling`
3. `TestGateRuleEdgeCases.test_gate_rule_evaluate_finding_none`
4. `TestSubstituteVariablesEdgeCases.test_substitute_variables_empty_dict`
5. `TestSubstituteVariablesEdgeCases.test_substitute_variables_none_variables`
6. `TestSubstituteVariablesEdgeCases.test_substitute_variables_boolean_values`
7. `TestSubstituteVariablesEdgeCases.test_substitute_variables_none_value`
8. `TestSubstituteVariablesEdgeCases.test_substitute_variables_numeric_values`
9. `TestRuleSetEdgeCases.test_rule_set_evaluate_missing_optional_attributes`
10. `TestRuleSetEdgeCases.test_rule_set_evaluate_disabled_rule_skipped`
11. `TestEnforcementEngineEdgeCases.test_evaluate_with_context_empty_variables`
12. `TestEnforcementEngineEdgeCases.test_evaluate_with_context_none_variables`
13. `TestEnforcementEngineEdgeCases.test_evaluate_with_context_multiple_variables`
14. `TestEnforcementEngineEdgeCases.test_evaluate_with_context_no_matching_rules`
15. `TestEnforcementEngineEdgeCases.test_evaluate_with_context_variable_in_when_and_message`

### Verification Commands (run in terminal):
```bash
cd /home/dimona/Dream-Pixels-Forge/Dev/cli/verdity
python -m pytest tests/test_enforcement.py -v
python -m pytest tests/test_enforcement.py --cov=src/verdity/enforcement --cov-report=term-missing
python -m pytest tests/ -v --cov=src/verdity --cov-report=term-missing
```

## Phase 2: GitHub Client (93% → 100%) - HIGH PRIORITY, BLOCKING
**File:** `src/verdity/github_client.py` (15 missing lines)
**Estimated:** 5-6 new test cases, 4 hours

### Missing Lines to Cover:
- `apply_fix()` method - error handling paths
- Branch creation failures
- Commit/push error scenarios
- File not found errors
- GitHub API rate limiting (403)
- Authentication failures (401)

## Phase 3: Version Module (69% → 100%) - LOW PRIORITY
**File:** `src/verdity/_version.py` (8 missing lines)
**Estimated:** 2-3 new test cases, 2 hours

### Missing Lines to Cover:
- Lines 24-26: Fallback path logic
- Lines 30-34: Installed package fallback

## Phase 4: MCP Server (70% → 100%) - MEDIUM PRIORITY
**File:** `src/verdity/mcp_server.py` (85 missing lines)
**Estimated:** 15-20 new test cases, 12 hours

## Phase 5: Worker/Gateway/Platforms/Approval Queue (86-99% → 100%) - MEDIUM/LOW PRIORITY
**Files:** `src/verdity/worker.py`, `src/verdity/gateway/app.py`, `src/verdity/platforms/*.py`, `src/verdity/approval_queue.py`
**Estimated:** 30-40 new test cases, 20 hours

## Phase 6: CLI Commands (30-89% → 100%) - MEDIUM PRIORITY
**Files:** `src/verdity/cli/review.py`, `src/verdity/cli/enforce.py`
**Estimated:** 20-25 new test cases, 16 hours

## Phase 7: Test Infrastructure - MEDIUM PRIORITY
**Estimated:** 8-10 fixtures, 4 hours

---

## Current Focus: Phase 1 - Enforcement Engine

### Delegation to implement-coder:
**Task:** Add tests to cover the 21 missing lines in `src/verdity/enforcement.py`

**Context file:** `.prides/context/I/phase1-enforcement-engine.md`

**Requirements:**
1. Read `src/verdity/enforcement.py` and `tests/test_enforcement.py`
2. Identify the exact uncovered lines
3. Add 4-6 new test cases to `tests/test_enforcement.py`
4. Run `pytest tests/test_enforcement.py --cov=src/verdity/enforcement --cov-report=term-missing` to verify 100% coverage
5. Ensure all existing 896+ tests still pass