# Workstream 4: Enhanced Enforcement Rules

## Task
Enhance enforcement engine with rule templating, priorities, groups, regex patterns, and CLI testing.

## Current State
- `src/verdity/enforcement.py` - Basic engine with CEL-like expressions
- `src/verdity/router.py` - Integration with router
- Tests in `tests/test_enforcement.py` (22 tests passing)

## Required Enhancements

### 1. Rule Templating/Variables
```python
# Support variables in rules
rule = GateRule(
    id="block-critical",
    when="finding.severity == 'critical' and finding.confidence > {{threshold}}",
    then=Action.BLOCK,
    message="CRITICAL finding blocks merge"
)

# Variables resolved at evaluation time
context = {
    "finding": finding,
    "threshold": 0.8,
    "org_policy": "strict"
}
```

### 2. Rule Priorities/Ordering
```python
@dataclass
class GateRule:
    id: str
    when: str
    then: Action
    message: str
    priority: int = 100  # Lower = higher priority
    enabled: bool = True
```

### 3. Rule Groups/Sets
```python
@dataclass
class RuleSet:
    name: str
    rules: list[GateRule]
    description: str
    
    def evaluate(self, finding: Finding) -> list[EnforcementDecision]:
        # Apply all rules in priority order
```

### 4. Regex Pattern Library
```python
# Built-in patterns
PATTERNS = {
    "secret": r"(api[_-]?key|secret|token|password)\s*[=:]\s*['\"][^'\"]+['\"]",
    "sql_injection": r"(?i)(union|select|insert|update|delete|drop)\s+.*['\"]",
    "xss": r"(?i)(<script|onerror=|onclick=|onload=)",
    "path_traversal": r"\.\./",
}

# Usage in rules
rule = GateRule(
    id="detect-secrets",
    when="regex_search(finding.content, PATTERNS['secret'])",
    then=Action.BLOCK,
    message="Potential secret detected"
)
```

### 5. Rule Testing CLI
```bash
# verdity-enforce test rules.yml --finding finding.json
# verdity-enforce validate rules.yml
# verdity-enforce test rules.yml --finding finding.json --verbose
```

## Implementation Tasks

### 1. Update `src/verdity/enforcement.py`
- Add `GateRule` with priority, enabled fields
- Add `RuleSet` class for grouping
- Add `evaluate_with_context` method
- Add regex pattern library
- Add variable substitution

### 2. Create CLI Module
```python
# src/verdity/cli/enforce.py
@click.group()
def enforce():
    pass

@enforce.command()
@click.argument("rules_file")
@click.option("--finding", type=click.Path(exists=True))
@click.option("--verbose", is_flag=True)
def test(rules_file, finding, verbose):
    """Test rules against a finding."""
```

### 3. Update Router Integration
- Pass rule context to enforcement engine
- Handle rule priorities in evaluation order
- Map enforcement actions to route actions

### 4. Update Router Tests
- Test rule prioritization
- Test variable substitution
- Test regex patterns
- Test rule groups

## Files to Modify
- `src/verdity/enforcement.py` - Core enhancements
- `src/verdity/cli/enforce.py` - New CLI module
- `src/verdity/router.py` - Integration updates
- `tests/test_enforcement.py` - New tests
- `tests/test_cli_enforce.py` - New test file

## Testing Requirements
- Rule evaluation with variables
- Priority ordering
- Regex pattern matching
- Rule group evaluation
- CLI command testing