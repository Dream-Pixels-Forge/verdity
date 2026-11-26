# ISS-008: Test Infrastructure for 100% Coverage

## Metadata
- **ID**: ISS-008
- **Phase**: I (Implement)
- **Severity**: MEDIUM
- **Status**: OPEN
- **Created**: 2026-10-03
- **Assigned**: implement-tasks
- **Blocking**: false

## Description
Create shared test infrastructure to enable 100% coverage across all modules.

## Required Infrastructure

### 1. GitHub API Mocking Fixtures
- Shared pytest fixtures for mocking `GitHubClient`
- Mock PR diff responses (success, 404, empty, large)
- Mock check run creation responses
- Mock rate limiting (403) and auth failure (401)

### 2. MCP Server Integration Test Helper
```python
# tests/conftest.py additions
async def call_mcp_tool(server, name, args):
    return await server.call_tool(name, args)

@pytest.fixture
async def mcp_server():
    server = create_mcp_server(InspectorConfig(), MultiModelFallback())
    await server.initialize()
    yield server
    await server.shutdown()
```

### 3. CLI Test Runner
```python
from click.testing import CliRunner

@pytest.fixture
def cli_runner():
    return CliRunner()

def test_verdity_review_run(cli_runner, mcp_server):
    with patch("verdity.mcp_server.create_mcp_server", return_value=mcp_server):
        result = cli_runner.invoke(review, ["run", "--owner", "org", "--repo", "repo", "--pr", "1"])
        assert result.exit_code == 0
```

### 4. Time Manipulation for SLA Tests
```python
import freezegun

@pytest.fixture
def frozen_time():
    with freezegun.freeze_time("2024-01-01 12:00:00") as frozen:
        yield frozen
```

### 5. Approval Queue Test Helpers
```python
@pytest.fixture
async def approval_queue():
    db = ApprovalQueue(":memory:")
    await db.connect()
    yield db
    await db.close()

@pytest.fixture
def sample_approval_item():
    return {
        "run_id": uuid.uuid4(),
        "finding_id": uuid.uuid4(),
        "repo_id": "test/repo",
        "concern": "security",
        "severity": "high",
        "file": "src/auth.py",
        "line_start": 42,
        "summary": "SQL injection",
        "explanation": "Direct concatenation",
        "confidence": 0.9,
        "route_action": "BLOCK",
    }
```

## Acceptance Criteria
- [ ] All fixtures added to `tests/conftest.py`
- [ ] `freezegun` added to dev dependencies
- [ ] Shared fixtures usable across all test modules
- [ ] Documentation in `tests/README.md`

## Related Files
- `tests/conftest.py`
- `pyproject.toml` (dev dependencies)
- `tests/README.md` (new)

## Dependencies
- `freezegun` package
- `pytest-asyncio` (already present)

## Estimated Effort
- 8-10 new fixtures/helpers
- ~4 hours
