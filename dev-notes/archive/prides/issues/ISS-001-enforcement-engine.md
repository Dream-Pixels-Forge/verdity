# ISS-001: Enforcement Engine Edge Case Coverage (84% → 100%)

## Metadata
- **ID**: ISS-001
- **Phase**: I (Implement)
- **Severity**: HIGH
- **Status**: OPEN
- **Created**: 2026-10-03
- **Assigned**: implement-coder
- **Blocking**: true

## Description
Enforcement engine in `src/verdity/enforcement.py` has 84% coverage. Missing 21 lines across critical edge cases.

## Missing Coverage Details

### GateRule.evaluate() (lines 42-44)
- Exception handling catches all exceptions, logs warning, returns False
- Broad `except Exception` masks real bugs
- **Tests needed**: 
  - Empty context finding
  - Malformed when expression
  - Regex search errors

### substitute_variables() (lines 59, 61)
- Line 59: Empty variables dict check not tested
- Line 61: Non-string value conversion (bool, None, int) not tested
- **Tests needed**:
  - Empty dict `{}`
  - None values in variables
  - Boolean values `true`/`false`
  - Integer values

### RuleSet.evaluate() (lines 158, 190-194)
- Finding proxy attribute access for missing optional attributes
- `explanation` and `content` attributes not tested when missing
- **Tests needed**:
  - Finding without `explanation`
  - Finding without `content`
  - Finding with only required attributes

### EnforcementEngine.evaluate_with_context() (lines 266-285)
- Edge cases with variable combinations not tested
- **Tests needed**:
  - Empty variables dict
  - Variables with special characters
  - Variable shadowing finding attributes

## Acceptance Criteria
- [ ] All 21 missing lines covered
- [ ] `pytest --cov=src/verdity/enforcement --cov-fail-under=100` passes
- [ ] No broad `except Exception` without specific handling

## Related Files
- `src/verdity/enforcement.py`
- `tests/test_enforcement.py`

## Dependencies
- None (foundational)

## Estimated Effort
- 4-6 new test cases
- ~4 hours
