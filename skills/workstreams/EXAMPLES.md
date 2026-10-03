# Workstreams Examples

Real-world examples for common development scenarios.

## Example 1: Full-Stack Feature Development

### Scenario
Implement user authentication feature with backend API, frontend UI, and database migration.

### Setup
```bash
# Initialize 3 workstreams
workstreams init --project user-auth \
  --workstreams 3 \
  --multiplexer tmux \
  --layout main-horizontal \
  --mode worktree \
  --shared-deps node_modules,.venv,.git
```

### Configuration (.workstreams.yaml)
```yaml
project: user-auth
multiplexer: tmux
layout: main-horizontal
base_branch: main
workstreams:
  - id: 1
    name: backend-api
    path: ./backend
    branch: ws/backend-auth
    command: "npm run dev"
    env:
      PORT: 3001
      DATABASE_URL: "postgresql://localhost/auth_dev_1"
  - id: 2
    name: frontend
    path: ./frontend
    branch: ws/frontend-auth
    command: "npm run dev"
    env:
      PORT: 3002
      VITE_API_URL: "http://localhost:3001"
  - id: 3
    name: database
    path: ./database
    branch: ws/db-auth
    command: "npm run migrate:dev"
    env:
      DATABASE_URL: "postgresql://localhost/auth_dev_1"
shared_deps:
  - node_modules
  - .venv
```

### Dispatch Subagents
```bash
# Dispatch specialized agents to each workstream
workstreams dispatch --workstream 1 --subagent backend-api --issue 42
workstreams dispatch --workstream 2 --subagent frontend-ui --issue 43
workstreams dispatch --workstream 3 --subagent database-schema --issue 44
```

### Monitor
```bash
workstreams status --live
```

---

## Example 2: Bug Fix Sprint

### Scenario
Fix 5 critical bugs across different modules in parallel.

### Setup
```bash
workstreams init --project bugfix-sprint \
  --workstreams 5 \
  --multiplexer tmux \
  --layout tiled \
  --mode worktree \
  --base-branch main
```

### Assign Issues
```bash
workstreams assign --workstream 1 --issue 101  # Auth bug
workstreams assign --workstream 2 --issue 102  # Payment bug
workstreams assign --workstream 3 --issue 103  # UI bug
workstreams assign --workstream 4 --issue 104  # API bug
workstreams assign --workstream 5 --issue 105  # DB bug
```

### Parallel Development
```bash
# Start all workstreams
workstreams start

# Monitor
workstreams status --live

# Check specific workstream
workstreams logs --workstream 2 --follow
```

### Sync & PR
```bash
# After fixes
workstreams sync --workstream 1
workstreams pr --workstream 1 --title "fix: resolve auth token refresh" --body "Closes #101"
```

---

## Example 3: Monorepo Feature Development

### Scenario
Large monorepo with backend, frontend, mobile, and shared packages.

### Configuration
```yaml
# .workstreams.yaml
project: myapp
multiplexer: tmux
layout: tiled
base_branch: main
mode: worktree
workstreams:
  - id: 1
    name: api
    path: ./packages/api
    branch: ws/api-v2
    command: "npm run dev"
    env:
      PORT: 4001
  - id: 2
    name: web
    path: ./apps/web
    branch: ws/web-redesign
    command: "npm run dev"
    env:
      PORT: 3000
  - id: 3
    name: mobile
    path: ./apps/mobile
    branch: ws/mobile-nav
    command: "expo start"
    env:
      EXPO_PORT: 8081
  - id: 4
    name: shared-ui
    path: ./packages/ui
    branch: ws/ui-tokens
    command: "npm run storybook"
    env:
      PORT: 6006
  - id: 4
    name: shared-utils
    path: ./packages/utils
    branch: ws/utils-date
    command: "npm run test:watch"
    env: {}

shared_deps:
  - node_modules
  - .turbo
  - .git
```

### Dispatch to Subagents
```bash
workstreams dispatch --workstream 1 --subagent backend-api --issue 101
workstreams dispatch --workstream 2 --subagent frontend-ui --issue 102
workstreams dispatch --workstream 3 --subagent mobile-app --issue 103
workstreams dispatch --workstream 4 --subagent ui-components --issue 104
```

---

## Example 4: Hotfix Pipeline

### Scenario
Critical production bug needs immediate fix with parallel investigation and fix.

### Setup
```bash
workstreams init --project hotfix-payment \
  --workstreams 3 \
  --multiplexer tmux \
  --layout even-horizontal \
  --mode branch \
  --base-branch hotfix/payment-critical
```

### Assign
```bash
workstreams assign --workstream 1 --issue 999  # Root cause analysis
workstreams assign --workstream 2 --issue 999  # Fix implementation
workstreams assign --workstream 3 --issue 999  # Testing & verification
```

### Parallel Investigation
```bash
# Workstream 1: Root cause analysis
workstreams dispatch --workstream 1 --subagent security --issue 999

# Workstream 2: Fix implementation
workstreams dispatch --workstream 2 --subagent backend-api --issue 999

# Workstream 3: Testing
workstreams dispatch --workstream 3 --subagent testing --issue 999
```

### Fast Sync & Deploy
```bash
# Quick sync
workstreams sync --workstream 2

# Create hotfix PR
workstreams pr --workstream 2 \
  --title "hotfix: fix payment validation race condition" \
  --body "Fixes #999 - Race condition in payment validation" \
  --base hotfix/payment-critical

# Fast-track merge
workstreams merge --workstream 2 --auto --method squash
```

---

## Example 5: Cross-Platform Feature

### Scenario
Feature needs implementation across Web, iOS, Android, and Backend.

### Setup
```bash
workstreams init --project cross-platform-feature \
  --workstreams 4 \
  --multiplexer tmux \
  --layout tiled \
  --mode worktree \
  --shared-deps node_modules,.venv,.git,pods
```

### Workstreams
| ID | Platform | Path | Branch | Command |
|----|----------|------|--------|---------|
| 1 | Backend | ./backend | ws/api-v3 | npm run dev |
| 2 | Web | ./web | ws/web-feature | npm run dev |
| 3 | iOS | ./ios | ws/ios-feature | xcodebuild |
| 4 | Android | ./android | ws/android-feature | ./gradlew assembleDebug |

### Shared Dependencies
```yaml
shared_deps:
  - node_modules
  - .venv
  - Pods
  - .git
  - .gradle
```

### Coordinated Testing
```bash
# Run integration tests across platforms
workstreams dispatch --workstream 1 --subagent testing --issue 201
workstreams dispatch --workstream 2 --subagent testing --issue 202
workstreams dispatch --workstream 3 --subagent testing --issue 203
workstreams dispatch --workstream 4 --subagent testing --issue 204

# Run E2E tests
workstreams run --workstream all --command "npm run test:e2e"
```

---

## Example 6: Refactoring Sprint

### Scenario
Large refactoring across multiple packages.

### Strategy
```bash
# 3 workstreams for different layers
workstreams init --project refactor-core \
  --workstreams 3 \
  --multiplexer zellij \
  --layout tabs \
  --mode worktree
```

### Workstream Assignment
| Workstream | Focus | Issues |
|------------|-------|--------|
| 1 | Data Layer | #201, #202, #203 |
| 2 | Business Logic | #204, #205 |
| 3 | API Layer | #206, #207 |

### Coordinated Refactoring
```bash
# Sync all before starting
workstreams sync --all

# Run tests in parallel
workstreams run --all --command "npm test"

# Sync after changes
workstreams sync --all

# Create coordinated PRs
workstreams pr --all --title-prefix "refactor: " --base main
```

---

## Example 7: CI/CD Pipeline Development

### Scenario
Build and deploy pipeline improvements.

### Setup
```bash
workstreams init --project ci-cd-improvements \
  --workstreams 4 \
  --multiplexer zellij \
  --layout tabs
```

| Workstream | Focus | Tools |
|------------|-------|-------|
| 1 | Build Optimization | Docker, BuildKit |
| 2 | Test Pipeline | Jest, Playwright |
| 3 | Deploy Pipeline | ArgoCD, Helm |
| 4 | Observability | Grafana, Loki |

### Pipeline Stages
```bash
# Parallel development
workstreams start

# Stage 1: Build optimization
workstreams dispatch --workstream 1 --subagent devops --issue 301

# Stage 2: Test improvements
workstreams dispatch --workstream 2 --subagent testing --issue 302

# Stage 3: Deploy automation
workstreams dispatch --workstream 3 --subagent devops --issue 303

# Stage 4: Monitoring
workstreams dispatch --workstream 4 --subagent sre --issue 304
```

---

## Example 7: Legacy Migration

### Scenario
Migrate legacy codebase to modern stack.

### Phased Approach
```bash
# Phase 1: Analysis (2 workstreams)
workstreams init --project legacy-migration \
  --workstreams 2 \
  --mode worktree

workstreams assign --workstream 1 --issue 401  # Inventory
workstreams assign --workstream 2 --issue 402  # Risk assessment

# Phase 2: Migration (4 workstreams)
workstreams scale --workstreams 4

workstreams assign --workstream 1 --issue 403  # Auth migration
workstreams assign --workstream 2 --issue 404  # API migration
workstreams assign --workstream 3 --issue 405  # DB migration
workstreams assign --workstream 4 --issue 406  # Frontend migration

# Phase 3: Validation
workstreams scale --workstreams 2
workstreams assign --workstream 1 --issue 407  # E2E tests
workstreams assign --workstream 2 --issue 408  # Performance
```

---

## Example 8: Open Source Contribution

### Scenario
Contribute to multiple related open source projects.

### Setup
```bash
workstreams init --project oss-contributions \
  --workstreams 3 \
  --multiplexer tmux \
  --mode worktree \
  --base-branch main

# Workstream 1: Core library
workstreams assign --workstream 1 --issue 101 --repo org/core-lib

# Workstream 2: CLI tool
workstreams assign --workstream 2 --issue 201 --repo org/cli-tool

# Workstream 3: Documentation
workstreams assign --workstream 3 --issue 301 --repo org/docs
```

### Coordinated Release
```bash
# Sync all
workstreams sync --all

# Create coordinated PRs
workstreams pr --all --title-prefix "feat: " --body-template "
Closes #ISSUE

## Changes
- CHANGE_DESCRIPTION

## Testing
- TEST_STEPS
"
```

---

## Advanced: Custom Workflow Scripts

### Pre-commit Hook
```bash
#!/bin/bash
# .git/hooks/pre-commit
workstreams sync --all --quiet
ruff check src/
pytest tests/ -q
```

### Auto-sync Daemon
```bash
#!/bin/bash
# scripts/auto-sync.sh
while true; do
  workstreams sync --all --quiet
  sleep 300  # 5 minutes
done
```

### Status Dashboard
```bash
#!/bin/bash
# scripts/dashboard.sh
watch -n 5 'workstreams status --format table --workstream all'
```

---

## CI/CD Integration

### GitHub Actions
```yaml
# .github/workflows/workstreams.yml
name: Workstreams CI

on:
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  workstream-test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        workstream: [1, 2, 3, 4]
    steps:
      - uses: actions/checkout@v4
      - name: Setup workstream
        run: |
          workstreams init --project ci-test --workstreams 4
          workstreams sync --workstream ${{ matrix.workstream }}
      - name: Run tests
        run: workstreams run --workstream ${{ matrix.workstream }} --command "pytest"
```

### Auto-merge on Green
```yaml
# .github/auto-merge.yml
on:
  pull_request:
    types: [labeled, synchronize]

jobs:
  auto-merge:
    if: github.event.label.name == 'auto-merge'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: |
          workstreams sync --workstream ${{ github.event.pull_request.number }}
          workstreams merge --workstream ${{ github.event.pull_request.number }} --auto
```

---

## Debugging Tips

### Common Issues
```bash
# Port conflicts
workstreams ports --workstream all

# Process stuck
workstreams kill --workstream 2 --force

# Stuck git operations
workstreams git --workstream 2 --command "reset --hard HEAD"

# Full reset
workstreams reset --workstream 2 --hard
```

### Debug Mode
```bash
# Verbose logging
WORKSTREAMS_DEBUG=1 workstreams start --verbose

# Dry run
workstreams start --dry-run

# Show commands without executing
workstreams init --project test --workstreams 2 --dry-run
```

---

## Performance Tips

1. **Share dependencies** - Use `--shared-deps` for node_modules, .venv, target/, Pods
2. **Use worktrees** - Faster than branch switching
4. **Parallel test execution** - `workstreams run --all --command "pytest -n auto"`
4. **Shallow clones** - `git clone --depth 1` for worktrees
5. **Sparse checkout** - `git sparse-checkout` for large repos