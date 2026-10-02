# Verdity Reference Documentation

Detailed API reference and implementation details for verdity v0.4.7+.

## Enforcement Engine (`src/verdity/enforcement.py`)

### Classes

#### `Action` (Enum)
```python
class Action(Enum):
    BLOCK = "block"
    REQUIRE_APPROVAL = "require_approval"
    ESCALATE = "escalate"
```

#### `GateRule` (dataclass)
```python
@dataclass
class GateRule:
    id: str
    when: str          # Python expression (restricted eval)
    then: Action
    message: str
    
    def evaluate(self, context: dict[str, Any]) -> bool:
        # Uses restricted eval with only safe built-ins
```

#### `EnforcementDecision` (dataclass)
```python
@dataclass
class EnforcementDecision:
    action: str          # "ALLOW", "BLOCK", "REQUIRE_APPROVAL", "ESCALATE"
    rule_id: str | None
    message: str
    
    @property
    def blocked(self) -> bool:
        return self.action.upper() in ("BLOCK", "REQUIRE_APPROVAL", "ESCALATE")
```

#### `EnforcementEngine`
```python
class EnforcementEngine:
    def __init__(self, rules: list[GateRule] | None = None):
        self.rules: list[GateRule] = rules or []

    def add_rule(self, rule: GateRule) -> None
    def remove_rule(self, rule_id: str) -> bool
    
    async def evaluate(self, finding: Any) -> EnforcementDecision:
        # Evaluates rules in order; first match wins
```

### Context for Rule Evaluation
```python
context = {
    "finding": {
        "severity": "critical",  # lowercase enum value
        "confidence": 0.9,
        "concern": "security",
        "file": "src/test.py",
        "line_start": 1,
        "line_end": 1,
        "summary": "Hardcoded secret"
    }
}
```

### Example Rules
```python
# Block CRITICAL with high confidence
rule = GateRule(
    id="block-critical-high-conf",
    when="finding.severity=='critical' and finding.confidence>0.8",
    then=Action.BLOCK,
    message="High confidence CRITICAL blocks merge"
)

# Require approval for HIGH severity
rule = GateRule(
    id="require-approval-high",
    when="finding.severity=='high' and finding.confidence>0.7",
    then=Action.REQUIRE_APPROVAL,
    message="High severity requires human approval"
)
```

---

## Router Integration (`src/verdity/router.py`)

### `route()` Function
```python
async def route(
    finding: Finding,
    calibrator: TrustCalibrator | None = None,
    context: dict[str, Any] | None = None,
    enforcement_engine: EnforcementEngine | None = None,
) -> RoutingDecision:
    # 1. Evaluate enforcement rules FIRST
    # 2. Then compute confidence with optional calibration
    # 3. Route based on confidence thresholds
```

### Integration Flow
```
Finding → Enforcement Engine (blocking rules) → 
  If blocked → MANUAL_REVIEW
  Else → Compute confidence (with calibrator) → 
    Route based on thresholds (AUTO_APPROVE ≥ 0.9, MANUAL_REVIEW ≥ 0.6)
```

---

## GitHub Checks API (`src/verdity/github_client.py`)

### `create_check_run`
```python
async def create_check_run(
    owner: str,
    repo: str,
    name: str,
    head_sha: str,
    status: str = "in_progress",  # "queued" | "in_progress" | "completed"
    conclusion: str | None = None,  # "success" | "failure" | "neutral" | ...
    output: dict[str, Any] | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any]:
```

### `update_check_run`
```python
async def update_check_run(
    owner: str,
    repo: str,
    check_run_id: int,
    status: str | None = None,
    conclusion: str | None = None,
    output: dict[str, Any] | None = None,
    completed_at: str | None = None,
) -> dict[str, Any]:
```

### Example Usage
```python
# Create check run
check_run = await client.create_check_run(
    owner="owner",
    repo="repo",
    name="verdity-review",
    head_sha="abc123",
    status="in_progress",
)

# Update with results
await client.update_check_run(
    owner="owner",
    repo="repo",
    check_run_id=check_run["id"],
    status="completed",
    conclusion="success",
    output={"title": "Verdity Review", "summary": "All checks passed"},
)
```

---

## Approval Queue SLA (`src/verdity/approval_queue.py`)

### `ApprovalItem` (dataclass)
```python
@dataclass
class ApprovalItem:
    id: str
    repo_id: str
    pr_number: int
    finding_id: str
    reason: str
    sla_hours: int = 24
    created_at: datetime | None = None
    escalated: bool = False
```

### `ApprovalQueue` (class)
```python
class ApprovalQueue:
    async def connect(self) -> None
    async def close(self) -> None
    
    async def add_item(self, item: ApprovalItem) -> None
    async def get_pending(self, repo_id: str | None = None, limit: int = 50) -> list[dict]
    async def check_sla_escalations(self) -> list[dict]
    async def get_item(self, item_id: str) -> ApprovalItem | None
    
    async def resolve(self, queue_id: str, reviewer_id: str, action: str, notes: str | None = None)
    async def stats(self, repo_id: str | None = None) -> dict[str, int]
```

### SLA Escalation Background Task
```python
# In Worker (src/verdity/worker.py)
async def _sla_escalation_loop(self):
    while self._running:
        await asyncio.sleep(self._sla_check_interval)  # default 1 hour
        if self._approval_queue:
            escalated = await self._approval_queue.check_sla_escalations()
            # Log escalated items
```

---

## Verification Gate Auto-Escalation (`src/verdity/verification_gate.py`)

### `GateVerdict` (dataclass)
```python
@dataclass
class GateVerdict:
    gate_id: uuid.UUID
    proposed_fix_id: uuid.UUID | None
    checks: list[GateCheck]
    passed: bool = True
    notes: str = ""
    escalated: bool = False
    escalation_scheduled: bool = False
```

### Auto-Escalation on Verifier Disagreement
```python
gate = VerificationGate()
result = await gate.run_checks(
    proposed_fix=fix,
    original_finding=finding,
    verifier=verifier,  # If verifier disagrees → escalated=True
    approval_queue=approval_queue  # Optional: schedule escalation
)

if result.escalated:
    # Auto-scheduled for human review
    pass
```

---

## Dynamic Version Management (`src/verdity/_version.py`)

### Usage
```python
import verdity
print(verdity.__version__)       # "0.4.7"
print(verdity.get_version())     # Same, dynamic from pyproject.toml
```

### Implementation
```python
# Reads version from pyproject.toml (single source of truth)
# Caches after first read
# Falls back to "0.0.0+unknown" if not found
```

---

## Budget Enforcer Specialist Budget (`src/verdity/budget_enforcer.py`)

### `SpecialistBudget`
```python
@dataclass
class SpecialistBudget:
    max_concurrent: int = 1
```

### `check_specialist_budget`
```python
async def check_specialist_budget(
    self,
    specialist_type: str,
    specialist_id: str,
    max_concurrent: int = 1,
) -> tuple[bool, list[str]]:
    """
    Returns (allowed, dropped_specialists)
    Enforces max_concurrent per specialist type
    """
```

### Example
```python
enforcer = BudgetEnforcer(te_service)
enforcer.set_budget("security", SpecialistBudget(max_concurrent=1))

allowed1, dropped1 = await enforcer.check_specialist_budget("security", "spec-1")
allowed2, dropped2 = await enforcer.check_budget("security", "spec-2")
# spec-1 dropped: dropped2 == ["spec-1"]
```

---

## Worker SLA Escalation (`src/verdity/worker.py`)

### Worker Initialization
```python
worker = Worker(
    queue=queue,
    orchestrator=orchestrator,
    max_concurrent=4,
    sla_check_interval=3600.0,  # 1 hour
    approval_queue=approval_queue,  # Optional
)
```

### SLA Escalation Loop
```python
async def _sla_escalation_loop(self):
    while self._running:
        await asyncio.sleep(self._sla_check_interval)
        if self._running and self._approval_queue:
            escalated = await self._approval_queue.check_sla_escalations()
            if escalated:
                logger.info("SLA escalation: %d items escalated", len(escalated))
```

### Manual Trigger
```python
escalated = await worker.check_sla_escalations()
```

---

## Event Queue Get Events (`src/verdity/event_queue.py`)

```python
async def get_events(self, limit: int = 100) -> list[dict]:
    """Get recent events from the queue for monitoring."""
```

---

## Testing Utilities

### `_make_finding` Helper
```python
def _make_finding(**kwargs) -> Finding:
    defaults = {
        "concern": ConcernType.SECURITY,
        "severity": Severity.HIGH,
        "file": "src/test.py",
        "line_start": 10,
        "line_end": 10,
        "summary": "Test finding",
        "explanation": "Test",
        "confidence": 0.8,
        "agent_version": "test@0.1.0",
        "prompt_hash": "sha256:abc123",
    }
    defaults.update(kwargs)
    return Finding(**defaults)
```

### Mock Verifier for Tests
```python
class DisagreeingVerifier:
    def verify(self, proposed_fix, original_finding):
        from verdity.verification_gate import GateCheck, CheckResult
        return GateCheck(
            name="matches_intent",
            result=CheckResult.FAIL,
            reason="Verifier disagrees with fix",
        )
```

---

## CI/CD Pipeline Reference

### `.github/workflows/ci.yml`
```yaml
name: CI
on:
  push:
    branches: [master]
  pull_request:
    branches: [master]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install "ruff>=0.6,<0.17"
      - run: ruff format --check src/ tests/
      - run: ruff check src/ tests/

  test:
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest]
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: ${{ matrix.python-version }} }
      - run: pip install -e ".[dev]"
      - run: pytest tests/ -v --cov=src/verdity --cov-report=term-missing
```

### Release Workflow (`.github/workflows/release.yml`)
```yaml
name: Release to PyPI
on:
  release:
    types: [published]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install build
      - run: python -m build
      - uses: actions/upload-artifact@v4
        with: { name: dist, path: dist/ }

  publish:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with: { name: dist, path: dist/ }
      - uses: pypa/gh-action-pypi-publish@release/v1
```

---

## Release Process

### 1. Bump Version
```bash
# Edit pyproject.toml
sed -i 's/version = "0.4.6"/version = "0.4.7"/' pyproject.toml

# Update __init__.py uses dynamic version (auto from pyproject.toml)
# No manual update needed!
```

### 2. Commit & Tag
```bash
git add pyproject.toml
git commit -m "chore(release): bump version to 0.4.7"
git push origin master
git tag v0.4.7
git push origin v0.4.7
```

### 3. GitHub Release
```bash
gh release create v0.4.7 --title "v0.4.7" --notes "Release notes..."
```

### 4. PyPI Auto-Publish
- Triggers on GitHub Release published
- Builds wheel + sdist
- Publishes to PyPI with attestations

---

## Common Patterns

### Adding New Enforcement Rule
```python
# 1. Add test in tests/test_enforcement.py
# 2. Create rule in enforcement.py
# 3. Run: pytest tests/test_enforcement.py -v

rule = GateRule(
    id="my-rule",
    when="finding.severity=='high' and finding.confidence>0.9",
    then=Action.REQUIRE_APPROVAL,
    message="High confidence HIGH requires approval"
)
engine.add_rule(rule)
```

### Adding GitHub Check Run
```python
# In orchestrator or router
check_run = await github_client.create_check_run(
    owner=owner,
    repo=repo,
    name="verdity-review",
    head_sha=sha,
    status="in_progress",
)

# ... process findings ...

await github_client.update_check_run(
    owner=owner,
    repo=repo,
    check_run_id=check_run["id"],
    status="completed",
    conclusion="success" if no_blockers else "failure",
    output={"title": "Verdity Review", "summary": summary},
)
```

### Running Tests
```bash
# All tests
pytest tests/ -v --cov=src/verdity --cov-report=term-missing

# Specific module
pytest tests/test_enforcement.py -v --tb=short

# With coverage
pytest tests/ --cov=src/verdity --cov-report=term-missing --cov-fail-under=100
```

### Formatting
```bash
ruff format src/ tests/
ruff check src/ tests/
```