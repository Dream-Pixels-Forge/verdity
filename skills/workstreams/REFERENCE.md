# Workstreams Reference Documentation

## API Reference

### workstreams init
```bash
workstreams init [OPTIONS]

Options:
  --project NAME          Project name (required)
  --workstreams N         Number of workstreams (default: 4)
  --multiplexer TYPE      tmux|zellij|tmuxp|wezterm|kitty (default: tmux)
  --layout LAYOUT         even-horizontal|even-vertical|main-horizontal|tiled (default: even-horizontal)
  --base-branch BRANCH    Base branch (default: main)
  --mode MODE             worktree|branch (default: worktree)
  --shared-deps LIST      Comma-separated paths to share
  --force                 Override existing configuration
```

### workstreams start
```bash
workstreams start [OPTIONS]

Options:
  --workstream ID         Start specific workstream
  --command CMD           Override default command
  --env KEY=VAL           Set environment variable
  --detach                Run in background
```

### workstreams status
```bash
workstreams status [OPTIONS]

Options:
  --workstream ID         Show specific workstream
  --verbose               Show detailed info
  --live                  Live updating status
  --format FORMAT         table|json|yaml (default: table)
```

### workstreams assign
```bash
workstreams assign [OPTIONS]

Options:
  --workstream ID         Target workstream (required)
  --issue NUM             GitHub issue number
  --issue LIST            Comma-separated issue numbers
  --task TEXT             Custom task description
```

### workstreams sync
```bash
workstreams sync [OPTIONS]

Options:
  --workstream ID         Target workstream
  --rebase                Use rebase instead of merge
  --force                 Force sync even with local changes
```

### workstreams pr
```bash
workstreams pr [OPTIONS]

Options:
  --workstream ID         Source workstream
  --title TEXT            PR title
  --body TEXT             PR body
  --base BRANCH           Target branch (default: main)
  --draft                 Create draft PR
```

### workstreams merge
```bash
workstreams merge [OPTIONS]

Options:
  --workstream ID         Workstream to merge
  --method METHOD         merge|squash|rebase (default: squash)
  --delete-branch         Delete branch after merge
  --auto                  Auto-merge when CI passes
```

### workstreams dispatch
```bash
workstreams dispatch [OPTIONS]

Options:
  --workstream ID         Target workstream
  --subagent TYPE         Subagent type: backend-api|frontend-ui|database-schema|testing|documentation|security|code-quality
  --issue NUM             GitHub issue to work on
  --prompt TEXT           Custom prompt for subagent
```

### workstreams logs
```bash
workstreams logs [OPTIONS]

Options:
  --workstream ID         Target workstream
  --follow                Follow logs live
  --lines N               Number of lines (default: 100)
  --since TIME            Show logs since timestamp
  --grep PATTERN          Filter logs
```

### workstreams sync
```bash
workstreams sync [OPTIONS]

Options:
  --workstream ID         Target workstream
  --rebase                Use rebase instead of merge
  --strategy STRATEGY     merge|rebase|fast-forward
```

### workstreams cleanup
```bash
workstreams cleanup [OPTIONS]

Options:
  --workstream ID         Clean specific workstream
  --all                   Clean all completed workstreams
  --force                 Force cleanup without confirmation
```

## Data Structures

### WorkstreamConfig
```python
@dataclass
class WorkstreamConfig:
    id: int
    name: str
    path: str
    branch: str
    command: str
    env: dict[str, str]
    multiplexer: str
    layout: str
    shared_deps: list[str]
    mode: str  # worktree|branch
```

### WorkstreamStatus
```python
@dataclass
class WorkstreamStatus:
    id: int
    name: str
    branch: str
    status: str  # running|stopped|error|pending
    pid: int | None
    git_status: str  # clean|dirty|ahead|behind|diverged
    last_commit: str
    last_sync: datetime | None
    current_task: str | None
    test_status: str  # passing|failing|running|unknown
    coverage: float
    last_activity: datetime
```

### WorkstreamsConfig
```python
@dataclass
class WorkstreamsConfig:
    project: str
    multiplexer: str
    layout: str
    base_branch: str
    workstreams: list[WorkstreamConfig]
    shared_deps: list[str]
    base_path: str
```

## Git Operations

### Worktree Operations
```bash
# Create worktree
git worktree add ../project-ws1 ws/backend

# Remove worktree
git worktree remove ../project-ws1

# List worktrees
git worktree list

# Prune stale
git worktree prune
```

### Branch Operations
```bash
# Create branch from base
git checkout -b ws/backend main

# Sync with base
git fetch origin main
git rebase origin/main

# Push
git push origin ws/backend
```

## Multiplexer Commands

### tmux
```bash
# Create session
tmux new-session -d -s project

# Create panes
tmux split-window -h -t project:0
tmux split-window -v -t project:0

# Layouts
tmux select-layout -t project even-horizontal
tmux select-layout -t project even-vertical
tmux select-layout -t project main-horizontal
tmux select-layout -t project tiled

# Send command to pane
tmux send-keys -t project:0.0 "npm run dev" Enter

# Attach
tmux attach -t project
```

### zellij
```bash
# Create session
zellij attach project --create

# New tab
zellij action new-tab

# New pane
zellij action new-pane

# Layout
zellij action toggle-floating-panes
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| WORKSTREAMS_PROJECT | Project name | - |
| WORKSTREAMS_MULTIPLEXER | Multiplexer type | tmux |
| WORKSTREAMS_LAYOUT | Layout | even-horizontal |
| WORKSTREAMS_BASE_BRANCH | Base branch | main |
| WORKSTREAMS_WORKTREE_MODE | worktree|branch | worktree |
| WORKSTREAMS_SHARED_DEPS | Shared dependencies | - |

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Invalid arguments |
| 3 | Git error |
| 4 | Multiplexer error |
| 5 | Workstream not found |
| 6 | Already running |
| 7 | Dirty working tree |
| 8 | Merge conflict |
| 9 | Permission denied |