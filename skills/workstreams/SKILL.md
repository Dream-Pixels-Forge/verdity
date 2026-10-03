---
name: workstreams
description: Manage parallel development workstreams across terminal multiplexers (tmux, zellij, tmuxp, zed, neovim terminals). Includes live monitoring dashboard, cross-process subagent event logging, and cross-terminal notifications.
version: 0.4.12
author: Dream-Pixels-Forge
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [workstreams, parallel-development, tmux, zellij, terminal-multiplexer, coding-agents, parallel-development, monitoring, subagents]
    related_skills: [subagent-driven-development, pipeline-orchestrator, test-driven-development, task-routing]
---

# Workstreams Skill

Manage parallel development workstreams across terminal multiplexers (tmux, zellij, tmuxp, zed, neovim terminals). Enables teams to run multiple coding agents in parallel across tmux panes, zellij tabs, or terminal sessions with live monitoring, subagent coordination, and cross-terminal notifications.

## Quick Start

```bash
# Initialize workstreams for a project
workstreams init --project myproject --workstreams 4

# Start workstreams across tmux panes
workstreams start --multiplexer tmux --layout even-horizontal

# Check status
workstreams status

# Attach to specific workstream
workstreams attach --workstream 2

# Live monitoring dashboard
workstreams monitor

# Dispatch subagent to workstream
workstreams dispatch --workstream 1 --subagent backend-api --issue 42
```

## Core Concepts

### Workstream
An isolated development environment with its own:
- Git branch
- Working directory (or shared with worktrees)
- Terminal session/pane
- Task assignment
- Independent test/run cycle

### Multiplexer Support
| Multiplexer | Command | Layout Options |
|-------------|---------|----------------|
| tmux | `tmux new-session -d -s project` | even-horizontal, even-vertical, main-horizontal, tiled |
| zellij | `zellij attach project` | tabs, panes, floating |

### New: Monitoring & Coordination
| Feature | Command | Description |
|---------|---------|-------------|
| Live Dashboard | `monitor` | Real-time TUI showing all workstreams |
| Subagent Events | `events` | View subagent activity across all workstreams |
| Cross-Terminal Notify | `notify` | Send desktop/file notifications |
| Attach Session | `attach` | Attach to existing multiplexer session |

## Workflows

### 1. Initialize Workstreams
```bash
workstreams init --project myproject \
  --workstreams 4 \
  --multiplexer tmux \
  --layout even-horizontal \
  --base-branch main
```

Creates:
- 4 git worktrees (or branches)
- 4 tmux panes in even-horizontal layout
- Each with independent git status

### 2. Assign Tasks
```bash
# Assign issue to workstream
workstreams assign --workstream 1 --issue 42

# Or bulk assign
workstreams assign --workstream 1 --issues 42,43,44
```

### 3. Start Development
```bash
workstreams start --multiplexer tmux

# Or start specific workstream
workstreams start --workstream 2 --cmd "npm run dev"
```

### 4. Monitor Progress (NEW)
```bash
# Live monitoring dashboard (Ctrl+C to exit)
workstreams monitor --refresh 2

# View subagent events from any terminal
workstreams events --since 10

# Follow logs from all workstreams
workstreams logs --workstream 1 --follow

# Send notification to other terminals
workstreams notify --title "Build done" --message "Workstream 1 completed" --urgency normal
```

### 5. Sync & Merge
```bash
# Sync workstream with main
workstreams sync --workstream 2

# Create PR from workstream
workstreams pr --workstream 2 --title "feat: add feature" --body "..."

# Merge after approval
workstreams merge --workstream 2
```

## Advanced Features

### Worktree vs Branch Mode
```bash
# Use git worktrees (isolated directories) - RECOMMENDED
workstreams init --mode worktree

# Use branches in same directory (faster, shared node_modules)
workstreams init --mode branch
```

### Shared Dependencies
```bash
# Share node_modules, target/, .venv across workstreams
workstreams init --shared-deps node_modules,target,.venv
```

### Custom Commands per Workstream
```yaml
# .workstreams.yaml
workstreams:
  - id: 1
    name: backend
    command: "cd backend && npm run dev"
    env:
      PORT: 3001
  - id: 2
    name: frontend
    command: "cd frontend && npm run dev"
    env:
      PORT: 3002
```

## Subagent Coordination (NEW)

Workstreams now supports cross-process subagent event logging. Subagents can report progress from any terminal/process:

### From CLI / Scripts
```bash
# Dispatch subagent (logs event automatically)
workstreams dispatch --workstream 1 --subagent backend-api --issue 42 --prompt "Fix auth"

# Subagent reports progress (from within workstream or any process)
python -c "
from workstreams import subagent_progress
subagent_progress('myproject', 1, 'backend-api', 42, 'Fixed login endpoint', {'files': 3})
"

# View events from main terminal
workstreams events --since 30
```

### Python API for Subagents
```python
from workstreams import (
    subagent_started,
    subagent_progress,
    subagent_completed,
    subagent_failed,
    subagent_error,
)

# Report lifecycle events
subagent_started("myproject", 1, "backend-api", 42, "Implement JWT auth")
subagent_progress("myproject", 1, "backend-api", 42, "Created auth middleware", {"files": 2})
subagent_completed("myproject", 1, "backend-api", 42, "Auth complete", {"tests": "passed"})

# Or generic
from workstreams import subagent_report
subagent_report("myproject", 1, "backend-api", 42, "progress", "Working...", {"step": 3})
```

Events are stored in `~/.workstreams/<project>/events.jsonl` and visible in:
- `workstreams monitor` (live dashboard)
- `workstreams events` (CLI)
- `workstreams status --live` (legacy)

## Integration with subagent-driven-development

| subagent-driven-development | workstreams |
|----------------------------|-------------|
| Single agent, sequential tasks | Multiple agents, parallel tasks |
| One PR at a time | Multiple PRs in parallel |
| Sequential TDD | Concurrent TDD across workstreams |
| Single terminal | Multiple terminals (tmux/zellij) |

### Combined Workflow
```bash
# 1. Plan with pipeline-orchestrator
pipeline-orchestrator plan --feature "user-auth"

# 2. Create workstreams for parallel implementation
workstreams init --project auth --workstreams 3

# 3. Dispatch subagents to each workstream
workstreams dispatch --workstream 1 --subagent backend-api
workstreams dispatch --workstream 2 --subagent frontend-ui
workstreams dispatch --workstream 3 --subagent database-schema

# 4. Monitor all from single terminal
workstreams monitor
# Or check events from any terminal
workstreams events
```

## Commands Reference

| Command | Description |
|---------|-------------|
| `init` | Initialize workstreams for project |
| `start` | Start all workstreams in multiplexer |
| `status` | Show status of all workstreams |
| `monitor` | **NEW** Live TUI monitoring dashboard |
| `attach` | **NEW** Attach to existing multiplexer session |
| `assign` | Assign issues/tasks to workstream |
| `sync` | Sync workstream with base branch |
| `pr` | Create PR from workstream |
| `merge` | Merge workstream PR |
| `dispatch` | Send subagent to workstream (logs event) |
| `run` | Run command in workstream |
| `logs` | View/follow workstream logs |
| `events` | **NEW** View subagent events |
| `notify` | **NEW** Send cross-terminal notification |
| `tail` | **NEW** Tail logs from all workstreams |

## Configuration

### .workstreams.yaml
```yaml
project: myproject
multiplexer: tmux
layout: even-horizontal
base_branch: main
workstreams:
  - id: 1
    name: backend
    path: ./backend
    branch: ws/backend
    command: "npm run dev"
    env:
      PORT: 3001
  - id: 2
    name: frontend
    path: ./frontend
    branch: ws/frontend
    command: "npm run dev"
    env:
      PORT: 3002
shared_deps:
  - node_modules
  - .venv
```

## Module Structure

```
workstreams/
├── __init__.py          # Public API exports
├── models.py            # Data classes (WorkstreamConfig, SubagentEvent, etc.)
├── config.py            # YAML config load/save
├── manager.py           # Core WorkstreamsManager
├── cli.py               # CLI argument parsing
├── event_log.py         # File-based cross-process event log
├── notifier.py          # Desktop/file notifications
├── dashboard.py         # Live TUI dashboard
├── subagent_client.py   # Subagent reporting helpers
└── multiplexer/
    ├── __init__.py      # Multiplexer factory
    ├── base.py          # Abstract base class
    ├── tmux.py          # Tmux implementation
    └── zellij.py        # Zellij implementation
```

## Best Practices

1. **Keep workstreams small** - 1-2 issues per workstream
2. **Use worktrees for isolation** - prevents conflicts
3. **Share dependencies** - saves disk space and install time
4. **Regular sync** - `workstreams sync` every hour
5. **Auto-merge on green** - configure auto-merge for green CI
6. **Clean up** - `workstreams cleanup` after merge
7. **Monitor from main terminal** - use `workstreams monitor` or `events`

## Troubleshooting

| Issue | Solution |
|-------|----------|
| tmux session not found | `workstreams init --force` |
| Port conflicts | Use different ports per workstream |
| Git conflicts | `workstreams sync --rebase` |
| Test interference | Isolate test databases per workstream |
| Events not showing | Check `~/.workstreams/<project>/events.jsonl` |

## References
- [REFERENCE.md](REFERENCE.md) - Detailed API
- [EXAMPLES.md](EXAMPLES.md) - Real-world examples
- [scripts/workstreams/](scripts/workstreams/) - Python package source
