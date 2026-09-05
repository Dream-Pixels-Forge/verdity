---
name: verdity
description: "Run Verdity security and code quality scans on PRs and local code. Use when scanning for secrets, CVEs, code quality issues, or test coverage gaps in agentic workflows."
version: 1.0.0
author: Dream-Pixels-Forge
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [verdity, scan, security, pr, ci-cd]
    related_skills: [verdity-usage, subagent-driven-development, pipeline-orchestrator]
---

# Verdity Skill

## Quick Start

```bash
# Install via npx skills add (from GitHub)
npx skills add github.com/Dream-Pixels-Forge/verdity/tree/main/skills/verdity

# Verify installation
verdity --version

# Scan a PR
verdity-scan --pr 42

# Scan local code
verdity-scan .
```

## Overview

Verdity is an AI-powered PR review and code security scanning system that detects hardcoded secrets, dependency CVEs, code quality issues, and test coverage gaps. This skill provides the command-line interface and integration patterns for triggering Verdity scans from agentic workflows.

## Installation

### Using npx skills add (recommended)

```bash
npx skills add github.com/Dream-Pixels-Forge/verdity/tree/main/skills/verdity

# Verify
verdity --version
```

### Alternative Methods

```bash
# From the PyPI package
pip install verdity

# From GitHub source
git clone https://github.com/Dream-Pixels-Forge/verdity.git
cd verdity
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

### Scan a PR

```bash
# Basic PR scan
verdity-scan --pr <pr-number>

# With verbose output
verdity-scan --pr <pr-number> --verbose

# With confidence threshold
verdity-scan . --min-confidence 0.7
```

### Scan Local Code

```bash
# Scan current directory
verdity-scan .

# Scan specific directory
verdity-scan src/

# With minimum confidence
verdity-scan . --min-confidence 0.5
```

### CI/CD Integration

```yaml
# .github/workflows/verdity.yml
name: Verdity Scan
on: [pull_request]

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npx skills add github.com/Dream-Pixels-Forge/verdity/tree/main/skills/verdity
      - run: verdity-scan --pr ${{ github.event.pull_request.number }}
        env:
          VERDITY_TOKEN: ${{ secrets.VERDITY_TOKEN }}
```

## Finding Types

Verdity detects:

| Type | Description | Action |
|------|-------------|--------|
| **Secret** | Hardcoded API keys, tokens, passwords | Block - must fix |
| **Dependency** | Known CVEs in dependencies | Block - upgrade |
| **Code Quality** | Style violations, complexity | Review |
| **Test Coverage** | Missing tests for critical paths | Add tests |
| **Documentation** | Missing docstrings, changelog | Update docs |

## Confidence Levels

| Score Range | Action | Description |
|-------------|--------|-------------|
| 0.8-1.0 | Auto-approve | Strong evidence, block merge |
| 0.5-0.8 | Manual review | Needs human judgment |
| 0.0-0.5 | Auto-dismiss | Likely false positive |

## Trust Boundary

Verdity is probabilistic, not deterministic. Treat findings as signals, not verdicts.

- **What to trust**: High-confidence secret detections, CVE findings
- **What to verify**: Code quality patterns, architectural suggestions
- **Never auto-merge** on low-confidence findings without review

## Anti-Patterns

| Anti-Pattern | Why It's Bad | Fix |
|--------------|--------------|-----|
| Blocking merge on low-confidence | Wastes developer time | Only block on high-confidence |
| Ignoring high-confidence findings | Security risks slip through | Always fix high-confidence |
| Dismissing without evidence | No audit trail | Always provide dismissal reason |
| Running too frequently | Wastes compute resources | Run on PR creation + updates only |

## License

MIT
