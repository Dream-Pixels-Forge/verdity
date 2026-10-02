# Verdity Examples

Practical examples for common verdity development tasks.

## Enforcement Engine Examples

### Basic Blocking Rule
```python
from verdity.enforcement import EnforcementEngine, GateRule, Action

engine = EnforcementEngine(rules=[])

# Block CRITICAL findings with high confidence
rule = GateRule(
    id="block-critical-high-confidence",
    when="finding.severity=='critical' and finding.confidence>0.8",
    then=Action.BLOCK,
    message="CRITICAL finding with high confidence blocks merge"
)
engine.add_rule(rule)

finding = Finding(
    concern=ConcernType.SECURITY,
    severity=Severity.CRITICAL,
    confidence=0.95,
    # ... other fields
)

decision = await engine.evaluate(finding)
# decision.action == "block"
# decision.rule_id == "block-critical-high-confidence"
```

### Multiple Rules - First Match Wins
```python
engine = EnforcementEngine(rules=[])

# Rule 1: Block all CRITICAL
engine.add_rule(GateRule(
    id="block-critical",
    when="finding.severity=='critical'",
    then=Action.BLOCK,
    message="All CRITICAL findings block"
))

# Rule 2: Require approval for HIGH
engine.add_rule(GateRule(
    id="require-approval-high",
    when="finding.severity=='high'",
    then=Action.REQUIRE_APPROVAL,
    message="HIGH requires approval"
))

# Rule 3: Escalate MEDIUM with high confidence
engine.add_rule(GateRule(
    id="escalate-medium-high-conf",
    when="finding.severity=='medium' and finding.confidence>0.9",
    then=Action.ESCALATE,
    message="High confidence MEDIUM escalates"
))

# CRITICAL finding → blocked by first rule
finding = Finding(severity=Severity.CRITICAL, confidence=0.5)
decision = await engine.evaluate(finding)
# decision.action == "block", decision.rule_id == "block-critical"
```

### Require Approval for High Confidence HIGH
```python
rule = GateRule(
    id="require-approval-high-conf",
    when="finding.severity=='high' and finding.confidence>0.8",
    then=Action.REQUIRE_APPROVAL,
    message="High confidence HIGH requires approval"
)
```

### Escalate Low Confidence CRITICAL
```python
rule = GateRule(
    id="escalate-low-conf-critical",
    when="finding.severity=='critical' and finding.confidence<0.5",
    then=Action.ESCALATE,
    message="Low confidence CRITICAL needs manual review"
)
```

### Complex Expressions
```python
# Multiple conditions with AND/OR
rule = GateRule(
    id="complex-rule",
    when="finding.severity=='critical' and finding.confidence>0.8 and finding.concern=='security'",
    then=Action.BLOCK,
    message="Security CRITICAL with high confidence blocks"
)

# Negation
rule = GateRule(
    id="allow-test-files",
    when="not finding.file.startswith('test_') and finding.severity=='critical'",
    then=Action.BLOCK,
    message="Block CRITICAL in non-test files"
)

# Confidence range
rule = GateRule(
    id="medium-confidence-range",
    when="finding.confidence >= 0.3 and finding.confidence <= 0.7",
    then=Action.ESCALATE,
    message="Medium confidence escalates"
)
```

---

## Router Integration Examples

### Using Enforcement with Router
```python
from verdity.router import route
from verdity.enforcement import EnforcementEngine, GateRule, Action
from verdity.trust_calibration import TrustCalibrator

# Setup enforcement engine
engine = EnforcementEngine(rules=[])
engine.add_rule(GateRule(
    id="block-critical",
    when="finding.severity=='critical'",
    then=Action.BLOCK,
    message="CRITICAL blocks"
))

# Setup calibrator
calibrator = TrustCalibrator(db_path=":memory:")
await calibrator.connect()
# ... train calibrator ...

# Route finding with enforcement + calibration
finding = Finding(
    concern=ConcernType.SECURITY,
    severity=Severity.CRITICAL,
    confidence=0.95,
    # ...
)

decision = await route(finding, calibrator=calibrator, enforcement_engine=engine)
# decision.action == RouteAction.MANUAL_REVIEW (mapped from BLOCK)
```

### Batch Routing with Enforcement
```python
from verdity.router import compute_batch_routing, RankedFinding
from verdity.enforcement import EnforcementEngine, GateRule, Action

engine = EnforcementEngine(rules=[...])

ranked_findings = [
    RankedFinding(finding=f1, rank_score=0.9),
    RankedFinding(finding=f2, rank_score=0.5),
]

results = compute_batch_routing(ranked_findings, enforcement_engine=engine)
# Results have enforcement applied first, then routing
```

---

## GitHub Checks API Examples

### Create Check Run
```python
from verdity.github_client import GitHubClient

client = GitHubClient(
    app_id=12345,
    private_key_pem=private_key,
    installation_id="98765",
)

# Create check run
check_run = await client.create_check_run(
    owner="myorg",
    repo="myrepo",
    name="verdity-review",
    head_sha="abc123def",
    status="in_progress",
    output={
        "title": "Verdity Code Review",
        "summary": "Review in progress...",
        "text": "Running security and quality checks..."
    }
)

check_run_id = check_run["id"]
```

### Update Check Run with Results
```python
# After processing findings
await client.update_check_run(
    owner="myorg",
    repo="myrepo",
    check_run_id=check_run_id,
    status="completed",
    conclusion="failure",  # or "success", "neutral"
    output={
        "title": "Verdity Code Review",
        "summary": f"Found {len(findings)} issues",
        "text": "\n".join([f"- {f.summary}: {f.explanation}" for f in findings]),
        "annotations": [
            {
                "path": f.file,
                "start_line": f.line_start,
                "end_line": f.line_end,
                "annotation_level": "failure" if f.severity == Severity.CRITICAL else "warning",
                "message": f.summary,
                "title": f.concern.value
            }
            for f in findings
        ]
    ),
    completed_at=datetime.now(UTC).isoformat()
)
```

### Complete Check Run Workflow
```python
async def run_review_with_checks(owner: str, repo: str, pr_number: int, head_sha: str):
    client = GitHubClient(...)
    
    # Create check run
    check_run = await client.create_check_run(
        owner=owner,
        repo=repo,
        name="verdity-review",
        head_sha=head_sha,
        status="in_progress",
        output={
            "title": "Verdity Review",
            "summary": "Starting review...",
        }
    )
    check_run_id = check_run["id"]
    
    try:
        # ... run verdity review ...
        findings = await run_review(...)
        
        # Determine conclusion
        has_blockers = any(f.severity == Severity.CRITICAL for f in findings)
        conclusion = "failure" if has_blockers else "success"
        
        # Update check run
        await client.update_check_run(
            owner=owner,
            repo=repo,
            check_run_id=check_run["id"],
            status="completed",
            conclusion="failure" if has_blockers else "success",
            output={
                "title": "Verdity Review Complete",
                "summary": f"Found {len(findings)} issues",
                "text": generate_summary(findings),
                "annotations": create_annotations(findings)
            }
        )
    except Exception as e:
        await client.update_check_run(
            owner=owner,
            repo=repo,
            check_run_id=check_run_id,
            status="completed",
            conclusion="failure",
            output={"title": "Error", "summary": str(e)}
        )
```

---

## SLA Escalation Examples

### Adding Items with SLA
```python
from verdity.approval_queue import ApprovalQueue, ApprovalItem
from verdity.audit_store import AuditStore
from datetime import datetime, UTC, timedelta

audit_store = AuditStore(db_path=":memory:")
await audit_store.connect()

approval_queue = ApprovalQueue(db_path=":memory:")
await approval_queue.connect()

# Add item with 24-hour SLA
item = ApprovalItem(
    id="item-123",
    repo_id="acme/widgets",
    pr_number=42,
    finding_id="finding-123",
    reason="CRITICAL: Hardcoded secret",
    sla_hours=24,
    created_at=datetime.now(UTC) - timedelta(hours=2),  # Already 2 hours old
)
await approval_queue.add_item(item)
```

### Check SLA Escalations
```python
# Manual trigger
escalated = await approval_queue.check_sla_escalations()
for item in escalated:
    print(f"Escalated: {item.id} - past SLA")
    # item.escalated == 1 (integer, not boolean)

# Get specific item
item = await approval_queue.get_item("item-123")
print(f"Escalated: {item.escalated}")  # 1 or 0
```

### Background SLA Check in Worker
```python
from verdity.worker import Worker
from verdity.orchestrator import Orchestrator

worker = Worker(
    queue=queue,
    orchestrator=orchestrator,
    sla_check_interval=3600.0,  # 1 hour
    approval_queue=approval_queue,
)

await worker.run_forever()

# Manual trigger
escalated = await worker.check_sla_escalations()
```

---

## Verification Gate Auto-Escalation

### Basic Verification Gate
```python
from verdity.verification_gate import VerificationGate, VerifierSubagent, GateVerdict

gate = VerificationGate()

# Run checks
verdict = gate.run_checks(
    proposed_fix=fix,
    original_finding=finding,
    verifier=verifier,  # Optional
    approval_queue=approval_queue,  # Optional, for escalation
)

# Check results
if verdict.passed:
    print("All checks passed")
else:
    for check in verdict.checks:
        if check.result == CheckResult.FAIL:
            print(f"Failed: {check.name} - {check.reason}")

# Check escalation
if verdict.escalated:
    print("Escalated to human review")
```

### Custom Verifier
```python
class MyVerifier:
    def verify(self, proposed_fix, original_finding):
        from verdity.verification_gate import GateCheck, CheckResult
        
        if proposed_fix.fix_type == "secret_removal":
            if "settings" in fix_code or "environ" in fix_code:
                return GateCheck(
                    name="matches_intent",
                    result=CheckResult.PASS,
                    reason="Fix uses config reference"
                )
            return GateCheck(
                name="matches_intent",
                result=CheckResult.FAIL,
                reason="Hardcoded secret not removed"
            )
        return GateCheck(
            name="matches_intent",
            result=CheckResult.SKIP,
            reason="Unknown fix type"
        )

verifier = MyVerifier()
verdict = gate.run_checks(fix, finding, verifier=verifier)
```

### Auto-Escalation with Approval Queue
```python
from verdity.approval_queue import ApprovalQueue
from verdity.audit_store import AuditStore

audit_store = AuditStore(db_path=":memory:")
await audit_store.connect()

approval_queue = ApprovalQueue(audit_store=audit_store)
await approval_queue.connect()

gate = VerificationGate()
verdict = gate.run_checks(
    proposed_fix=fix,
    original_finding=finding,
    verifier=verifier,
    approval_queue=approval_queue  # Auto-schedules escalation
)

if verdict.escalated:
    # Escalation scheduled in approval queue
    assert verdict.escalation_scheduled
```

---

## GitHub Checks API Complete Workflow

### Full PR Review with Checks
```python
async def review_pr_with_checks(
    github_client: GitHubClient,
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    findings: list[Finding]
):
    # 1. Create check run
    check_run = await github_client.create_check_run(
        owner=owner,
        repo=repo,
        name="verdity-review",
        head_sha=head_sha,
        status="in_progress",
        output={
            "title": "Verdity Code Review",
            "summary": "Running automated review...",
            "text": f"Reviewing PR for {len(findings)} findings"
        }
    )
    check_run_id = check_run["id"]
    
    try:
        # Process findings
        has_critical = any(f.severity == Severity.CRITICAL for f in findings)
        has_high = any(f.severity == Severity.HIGH for f in findings)
        
        # Build output
        annotations = []
        for f in findings:
            annotations.append({
                "path": f.file,
                "start_line": f.line_start,
                "end_line": f.line_end,
                "annotation_level": "failure" if f.severity in (Severity.CRITICAL, Severity.HIGH) else "warning",
                "message": f.summary,
                "title": f.concern.value
            })
        
        conclusion = "failure" if any(f.severity in (Severity.CRITICAL, Severity.HIGH) for f in findings) else "success"
        
        # Update check run
        await github_client.update_check_run(
            owner=owner,
            repo=repo,
            check_run_id=check_run_id,
            status="completed",
            conclusion="success" if not has_critical else "failure",
            output={
                "title": "Verdity Code Review",
                "summary": f"Found {len(findings)} issues",
                "text": f"CRITICAL: {sum(1 for f in findings if f.severity==Severity.CRITICAL)}, HIGH: {sum(1 for f in findings if f.severity==Severity.HIGH)}",
                "annotations": annotations[:50]  # GitHub limit
            }
        )
        
        return conclusion == "success"
    except Exception as e:
        await client.update_check_run(
            owner=owner,
            repo=repo,
            check_run_id=check_run_id,
            status="completed",
            conclusion="failure",
            output={"title": "Error", "summary": str(e)}
        )
        raise
```

---

## SLA Escalation Complete Flow

### Setup
```python
# 1. Setup components
audit_store = AuditStore(db_path=":memory:")
await audit_store.connect()

event_queue = EventQueue(db_path=":memory:")
await event_queue.connect()

metrics_store = MetricsStore(db_path=":memory:")
await metrics_store.connect()

approval_queue = ApprovalQueue(db_path=":memory:")
await approval_queue.connect()

# 2. Create worker with SLA check
worker = Worker(
    queue=event_queue,
    orchestrator=orchestrator,
    max_concurrent=4,
    sla_check_interval=3600.0,  # 1 hour
    approval_queue=approval_queue,
)

# 3. Run worker (includes SLA checks)
await worker.run_forever()
```

### Manual SLA Check
```python
# Trigger manual SLA check
escalated = await approval_queue.check_sla_escalations()
for item in escalated:
    print(f"Escalated: {item.id} ({item.repo_id}#{item.pr_number})")
    # Notify team, create GitHub issue, etc.
```

---

## Verification Gate with Auto-Escalation

### Complete Setup
```python
from verdity.verification_gate import VerificationGate
from verdity.coding_agent import ProposedFix
from verdity.approval_queue import ApprovalQueue
from verdity.audit_store import AuditStore
from verdity.schemas import Finding

# Setup
audit_store = AuditStore(db_path=":memory:")
await audit_store.connect()
approval_queue = ApprovalQueue(audit_store=audit_store)
await approval_queue.connect()

gate = VerificationGate()

# Create finding and proposed fix
finding = Finding(
    concern=ConcernType.SECURITY,
    severity=Severity.CRITICAL,
    file="src/auth.py",
    line_start=10,
    line_end=10,
    summary="Hardcoded API key",
    explanation="API key hardcoded in source",
    confidence=0.95,
    # ...
)

proposed_fix = ProposedFix(
    finding_id=finding.finding_id,
    file="src/auth.py",
    original_line=10,
    suggested_lines=['API_KEY = os.environ.get("API_KEY")'],
    explanation="Replace hardcoded key with environment variable",
    fix_type="secret_removal",
)

# Run verification
verdict = gate.run_checks(
    proposed_fix=proposed_fix,
    original_finding=finding,
    verifier=verifier,  # Optional
    approval_queue=approval_queue  # Enable auto-escalation
)

if verdict.escalated:
    # Automatically added to approval queue for human review
    print("Escalated to human review")
```

---

## Dynamic Version Examples

### Reading Version
```python
import verdity

print(verdity.__version__)       # "0.4.7"
print(verdity.get_version())     # Same

# Direct access
from verdity._version import get_version
print(get_version())  # "0.4.7"
```

### In Templates/Scripts
```python
# In templates
version = verdity.get_version()
template = f"Verdity v{version} - Review complete"
```

---

## Budget Enforcer Specialist Budget

### Setup
```python
from verdity.budget_enforcer import BudgetEnforcer, SpecialistBudget
from verdity.token_economics import TokenEconomicsService

te_service = TokenEconomicsService()
budget_enforcer = BudgetEnforcer(te_service=te_service)

# Set budget for specialist type
budget_enforcer.set_budget("security", SpecialistBudget(max_concurrent=2))
budget_enforcer.set_budget("code_quality", SpecialistBudget(max_concurrent=1))
```

### Check Budget
```python
# First request - allowed
allowed1, dropped1 = await budget_enforcer.check_specialist_budget("security", "spec-1")
# allowed1=True, dropped1=[]

# Second request - drops first
allowed2, dropped2 = await budget_enforcer.check_specialist_budget("security", "spec-2")
# dropped2 == ["spec-1"]
```

---

## Testing Patterns

### Fixture for Enforcement Tests
```python
@pytest_asyncio.fixture
async def enforcement_fixture():
    from verdity.enforcement import EnforcementEngine, GateRule, Action
    from verdity.github_client import GitHubClient
    from verdity.approval_queue import ApprovalQueue
    from verdity.budget_enforcer import BudgetEnforcer
    from verdity.token_economics import TokenEconomicsService
    from verdity.audit_store import AuditStore
    from verdity.event_queue import EventQueue
    from verdity.metrics_store import MetricsStore

    # Set env vars
    os.environ["GITHUB_APP_ID"] = "12345"
    os.environ["GITHUB_APP_INSTALLATION_ID"] = "98765"
    os.environ["GITHUB_APP_PRIVATE_KEY"] = "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----"

    audit_store = AuditStore(db_path=":memory:")
    await audit_store.connect()
    event_queue = EventQueue(db_path=":memory:")
    await event_queue.connect()
    metrics_store = MetricsStore(db_path=":memory:")
    await metrics_store.connect()
    approval_queue = ApprovalQueue(db_path=":memory:")
    await approval_queue.connect()

    github_client = GitHubClient(
        app_id=12345,
        private_key_pem="-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----",
        installation_id="98765",
    )

    te_service = TokenEconomicsService()
    enforcement_engine = EnforcementEngine(rules=[])
    budget_enforcer = BudgetEnforcer(te_service=te_service)

    yield {
        "enforcement_engine": enforcement_engine,
        "github_client": github_client,
        "approval_queue": approval_queue,
        "budget_enforcer": budget_enforcer,
        "audit_store": audit_store,
        "event_queue": event_queue,
        "metrics_store": metrics_store,
    }

    await audit_store.close()
    await event_queue.close()
    await metrics_store.close()
    await approval_queue.close()
```

### Making Findings
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

# Usage
finding = _make_finding(severity=Severity.CRITICAL, confidence=0.9)
```

---

## Running Tests

```bash
# All enforcement tests
pytest tests/test_enforcement.py -v --tb=short

# Specific test
pytest tests/test_enforcement.py::TestEnforcementEngine::test_engine_blocks_critical_high_confidence -v

# With coverage
pytest tests/test_enforcement.py --cov=src/verdity --cov-report=term-missing

# Full suite
pytest tests/ -v --cov=src/verdity --cov-report=term-missing --cov-fail-under=100
```

### Formatting
```bash
ruff format src/ tests/
ruff check src/ tests/
```