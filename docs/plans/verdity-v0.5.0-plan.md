# Verdity v0.5.0 Roadmap Plan

## Overview
Parallel development of 4 independent workstreams for v0.5.0 release.

## Workstreams

### WS1: Coverage Gap Closure (100% Coverage)
**Priority:** Critical | **Effort:** Medium | **Dependencies:** None
- Fix 54 uncovered lines (budget_enforcer, event_queue, async_sqlite, etc.)
- Target: 100% coverage (currently 98.5%)
- Files: budget_enforcer.py, event_queue.py, async_sqlite.py, etc.

### WS2: GitLab & Bitbucket Platform Support
**Priority:** High | **Effort:** High | **Dependencies:** None
- Implement GitLab platform adapter (webhook, API, MR comments)
- Implement Bitbucket platform adapter (webhook, API, PR comments)
- Shared platform abstraction layer
- Tests for both platforms

### WS3: Enhanced GitHub Checks API
**Priority:** High | **Effort:** Medium | **Dependencies:** None
- Inline annotations (line-level feedback)
- Markdown summary with formatting
- Action buttons (re-run, dismiss)
- Auto-fix suggestions in check output

### WS4: Enhanced Enforcement Rules
**Priority:** Medium | **Effort:** Medium | **Dependencies:** WS1 (for testing)
- Rule templating/variables
- Rule priorities/ordering
- Rule groups/sets
- Regex pattern library
- Rule testing CLI: `verdity-enforce test rules.yml`

## Dependency Graph
```
WS1 (Coverage) ──────┐
                      ├─→ WS4 (Enforcement Rules) [needs WS1 for testing]
WS2 (GitLab/Bitbucket) ──── Independent
WS3 (GitHub Checks) ──── Independent
```

## Parallel Groups
- **Group 1 (No deps):** WS1, WS2, WS3
- **Group 2 (Depends on WS1):** WS4

## Success Criteria
- 100% test coverage
- GitLab + Bitbucket webhook handling
- GitHub Checks with annotations + auto-fix
- Rule CLI with test command
- All 805+ tests passing, 100% coverage
