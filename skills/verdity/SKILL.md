---
name: verdity
description: AI-powered PR review system with enforcement engine, GitHub Checks API, SLA escalation, and dynamic versioning. Use when working on verdity codebase, implementing PR review automation, or setting up CI/CD pipelines for code review.
---

# Verdity Skill

Verdity is an AI-powered PR review system with enforcement engine, GitHub Checks API, SLA escalation, and dynamic version management. This skill covers development workflows for the verdity codebase.

## Quick Start

```bash
# Install verdity in development mode
pip install -e ".[dev]"

# Run tests
pytest tests/ -v --cov=src/verdity --cov-report=term-missing

# Run specific test module
pytest tests/test_enforcement.py -v
```

## PR-Driven Development Workflow

### 1. Create GitHub Issue
```bash
gh issue create --title "feat: Your feature" --body "## Task\n- Task details\n## Acceptance Criteria\n- [ ] Criteria 1\n## Plan Reference\nFrom review: Priority X"
```

### 2. Create Branch from Master
```bash
git checkout master && git pull origin master
git checkout -b feature/<issue-number>-<slug>
```

### 3. Implement with TDD
```bash
# Write failing tests FIRST
pytest tests/test_<module>.py -v --tb=short

# Write minimal implementation
# Run tests to verify PASS
pytest tests/ -v --tb=short

# Run full test suite
pytest tests/ -q
```

### 4. Format & Lint
```bash
ruff format src/ tests/
ruff check src/ tests/
```

### 5. Push & Create PR
```bash
git add -A && git commit -m "feat(#<issue>): Description"
git push origin feature/<issue>-<slug>
gh pr create --title "feat(#<issue>): Description" --body "## Changes\n- ...\n## Linked Issue\nCloses #<issue>"
```

### 6. CI Must Pass
All checks must pass before merge:
- Lint & format (ruff)
- Tests (805 tests, 98.5%+ coverage)
- CI/CD pipeline

## New Features in v0.4.7+

### Dynamic Version Management
```python
import verdity
print(verdity.__version__)  # 0.4.7
print(verdity.get_version())  # Same, reads from pyproject.toml
```

### Enforcement Engine (Issue #42)
```python
from verdity.enforcement import EnforcementEngine, GateRule, Action

engine = EnforcementEngine(rules=[])
rule = GateRule(
    id="block-critical",
    when="finding.severity=='critical' and finding.confidence>0.8",
    then=Action.BLOCK,
    message="High confidence CRITICAL blocks"
)
engine.add_rule(rule)

decision = await engine.evaluate(finding)
# decision.action -> "block" | "require_approval" | "escalate" | "allow"
```

### GitHub Checks API
```python
from verdity.github_client import GitHubClient

client = GitHubClient(...)
await client.create_check_run(owner, repo, name, head_sha, status="in_progress")
await client.update_check_run(owner, repo, check_run_id, status="completed", conclusion="success")
```

### SLA Escalation
```python
# Approval queue items have sla_hours field
await approval_queue.check_sla_escalations()
# Background task in Worker runs hourly
```

## Testing Standards

- **TDD mandatory**: Write failing tests first
- **Coverage**: 98%+ required (fail-under=100 in pyproject.toml)
- **Pragma no cover**: Use `# pragma: no cover` for defensive code only

## CI/CD Pipeline

```yaml
# .github/workflows/ci.yml
- Lint & format (ruff)
- Tests (ubuntu/windows, py3.11/3.12/3.13)
- Coverage fail-under=100
```

## References
- See [REFERENCE.md](REFERENCE.md) for detailed API docs
- See [EXAMPLES.md](EXAMPLES.md) for more examples