# Contributing to Verdity

Thank you for considering contributing to Verdity. This document covers everything you need to get started.

## Development Environment Setup

### Prerequisites

- Python 3.11 or newer
- Git
- pip

### 1. Clone and install

```bash
git clone https://github.com/Dream-Pixels-Forge/verdity.git
cd verdity
pip install -e ".[dev]"
```

This installs Verdity in editable mode along with all dev dependencies (pytest, pytest-asyncio, pytest-cov).

### 2. Environment variables

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

Refer to `.env.example` for required variables.

## Running Tests

```bash
# Run the full test suite
pytest tests/ -v

# Run with coverage report
pytest tests/ -v --cov=src/verdity --cov-report=term-missing

# Run a specific test file
pytest tests/test_webhook_normalizer.py -v

# Run tests matching a pattern
pytest tests/ -v -k "hmac"
```

The project enforces 100% code coverage (`--cov-fail-under=100` in `pyproject.toml`). All new code must include tests.

## Linting

Verdity uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting.

```bash
# Check for lint errors
ruff check src/ tests/

# Auto-fix fixable issues
ruff check --fix src/ tests/

# Format code
ruff format src/ tests/
```

## Submitting Changes

### Branch naming

Use descriptive branch names:

- `feat/webhook-retry` — new features
- `fix/hmac-validation` — bug fixes
- `docs/api-reference` — documentation

### Commit messages

Write clear, concise commit messages:

- `feat: add retry logic for webhook delivery`
- `fix: validate HMAC signature before processing`
- `docs: update API reference for webhook endpoints`

### Pull request checklist

Before opening a PR, verify:

- [ ] All tests pass (`pytest tests/ -v`)
- [ ] No lint errors (`ruff check src/ tests/`)
- [ ] New code has corresponding tests
- [ ] Coverage remains at 100%
- [ ] Type hints are present on all public functions
- [ ] Docstrings explain non-obvious logic

## Code Style

### Ruff

Ruff is configured in `pyproject.toml` with a target of Python 3.11 and a line length of 100. All code must pass `ruff check` and `ruff format`.

### Type hints

All public functions, methods, and return values must have type hints:

```python
async def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify HMAC-SHA256 signature against payload."""
    ...
```

Use standard library types (`collections.abc`, `typing`) over third-party type libraries.

### Async patterns

Verdity is async-first. Use `async`/`await` for I/O-bound operations:

```python
async def process_event(event: WebhookEvent) -> ReviewResult:
    """Process a webhook event through the review pipeline."""
    async with httpx.AsyncClient() as client:
        response = await client.post(review_url, json=event.model_dump())
        return ReviewResult.model_validate(response.json())
```

- Use `asyncio.gather` for concurrent independent operations
- Avoid blocking calls inside async functions — offload with `asyncio.to_thread` if needed
- Use `pytest-asyncio` with `asyncio_mode = "auto"` for tests

### Pydantic

Use Pydantic v2 models for all data structures:

```python
class WebhookPayload(BaseModel):
    """Incoming webhook payload from GitHub."""

    action: str
    pull_request: PullRequest
    repository: Repository
```

## Project Structure

```
src/verdity/
├── __init__.py
├── worker.py          # Entry point for the review worker
├── models.py          # Pydantic data models
├── config.py          # Settings and environment
├── gateway.py         # FastAPI application
├── client/
│   └── github.py      # GitHub API client
├── reviewer/
│   └── orchestrator.py # Review orchestration pipeline
├── security/
│   ├── hmac.py        # HMAC signature verification
│   └── audit.py       # Audit log storage
└── utils/
    ├── rate_limiter.py
    └── event_queue.py
```

## Questions?

Open an issue at https://github.com/Dream-Pixels-Forge/verdity/issues for questions or discussion.
