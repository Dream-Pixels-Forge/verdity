# Verdity Skill

## Installation

### Using npx skills add (recommended)

```bash
# Install the Verdity skill from GitHub
npx skills add github.com/Dream-Pixels-Forge/verdity/tree/main/skills/verdity

# Verify installation
verdity --version
```

### Alternative installation methods

```bash
# Using pip (Python backend)
pip install verdity

# From source
git clone https://github.com/Dream-Pixels-Forge/verdity.git
cd verdity
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Quick Start

```bash
# Scan a PR
verdity-scan --pr 42

# Scan local code directory
verdity-scan .

# Scan with confidence threshold
verdity-scan . --min-confidence 0.7
```

## Features

- Scan PRs for security vulnerabilities
- Detect hardcoded secrets (API keys, tokens, passwords)
- Identify dependency CVEs
- Code quality analysis
- Test coverage gap detection
- Deterministic confidence scoring (not LLM-dependent)
- Auto-approve, manual review, or auto-dismiss routing

## Configuration

Set required environment variables:

```bash
export VERDITY_TOKEN=your_github_token_here
export WEBHOOK_HMAC_SECRET=your_hmac_secret
```

## Trust Boundary

Verdity is probabilistic, not deterministic. Treat findings as signals, not verdicts.

- **What to trust**: High-confidence secret detections, CVE findings
- **What to verify**: Code quality patterns, architectural suggestions
- **Never auto-merge** on low-confidence findings without review

## License

MIT
