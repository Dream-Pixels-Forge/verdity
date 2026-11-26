# Handoff: Verdity

**Date**: 2026-10-05
**Version**: 0.4.17
**Branch**: `chore/honest-coverage` (local), tracking `origin/chore/honest-coverage`
**State**: one open PR awaiting human review (green), one merged.

---

## Executive summary

Verdity's `master` CI had been red. It is now green — and the coverage gate that
gated it turned out to be reporting a number that was partly fiction.

The headline: `master` claimed **100% coverage** because **114 `# pragma: no cover`
comments** told coverage not to look. Stripping them revealed the real figure was
**98.776%** — 55 uncovered lines across 7 files, mostly reachable logic that had
been excluded rather than tested.

Closing that gap surfaced **two real bugs**, a **CI-only failure mode** invisible
from a single interpreter, and a body of genuinely dead code.

| | before | after |
|---|---|---|
| Pragmas hiding code | 114 | **3** |
| Real coverage | 98.776% | **100%** |
| Tests | 1062 | **1124** |
| Warnings | 15 | **0** |
| `master` CI | 0/7 jobs passing | **7/7** |

---

## Repository state

```
master   5872a68  fix(ci): restore green master — SLA bug, coverage gate, ruff lint (#62)
HEAD     befa317  test(gateway): cover both webhook handlers on Python 3.11 too
         857ffcf  test(gateway): stop lifespan tests corrupting shared app.state
         d1271ec  test: make the 100% coverage gate honest (114 pragmas -> 3)
```

### PRs (mine)

| PR | Title | Checks |
|---|---|---|
| [#62](https://github.com/Dream-Pixels-Forge/verdity/pull/62) | **MERGED** — restored green master | 7/7 |
| [#64](https://github.com/Dream-Pixels-Forge/verdity/pull/64) | Make the 100% coverage gate honest | 7/7 |

`#64` is `CLEAN`, mergeable, and **independent of #62**. It does not overlap
`#36`–`#39`.

### PR #61 was closed, deliberately

`#61` (review CLI auth + orchestrator wiring) was closed rather than merged. Its
five commits are already in `master` via #62's squash, and merging the branch would
have **regressed** master — it still carried the orphaned dead code and the broken
`post_check_run()` call. Reasoning is recorded on the PR.

---

## What was actually fixed

### 1. SLA escalation fired immediately (the important one)

`check_sla_escalations()` compared SQLite's `datetime(created_at, ...)` — which
yields `YYYY-MM-DD HH:MM:SS` — against a raw ISO-8601 cutoff containing `T` and
`+00:00`. The comparison is textual, and a space sorts before `T`, so **every
pending approval item looked overdue regardless of its SLA**.

Fixed by wrapping the cutoff in `datetime()`. The regression test
`test_sla_boundary_respects_both_directions` pins both directions and was verified
to fail against the old query.

### 2. Three MCP bugs on the GitHub posting path

Found by running `review run` against a real PR instead of only against mocks.
AsyncMock invents any attribute, so all three were invisible to the suite:

- **`post_to_github` crashed on every review with findings.** `Finding` needs 7 more
  fields than the flattened dict provides. Since `--post-comment` defaults to
  **True**, `verdity-review run` failed on its default path for any PR with
  findings — the exact case the tool exists to report.
- **`client.post_check_run()` does not exist.** The real method is
  `create_check_run()`. Added a guard test asserting method names against the real
  class so a mock cannot hide this again.
- **`apply_fix` was an advertised MCP tool that could only fail.** No such method on
  `GitHubClient`, plus an unauthenticated client. Now returns an explicit "not
  implemented" instead of an `AttributeError`. Implementing the write path is real
  work deserving its own PR.

### 3. Prompt-injection findings always reported line 1

The scanner located hits by searching each line for the pattern *name*
(`ignore_instructions`) — an internal label that never appears in source text, since
the payload reads `ignore all previous instructions`. The comparison could never
match, so every finding fell through to `line_start = 1`. An injection on line 200
was reported as line 1.

`PromptInjectionResult` now carries `match_start`, and the scanner resolves the line
from the offset.

### 4. Dead code removed

- `budget_enforcer.get_spend_summary()` held a stray copy of the tail of
  `check_specialist_budget()` **after its `return`**, referencing `active` and
  `specialist_id` — names that do not exist in that scope.
- `mcp_server._review_full()` / `_verdity_review()` each built a `SpecialistContext`
  and `ReviewPolicy` that was never passed on — leftovers from the earlier wiring
  change to the real orchestrator API.
- `_parse_ip_allowlist` had a "bare IP without CIDR" fallback behind `ip_network()`,
  which already accepts `10.0.0.1`. Verified unreachable: no input exists where
  `ip_address()` succeeds but `ip_network()` fails.
- `get_repo_dashboard` had an unreachable guard (`get_repo_summary` raises first).

### 5. A test that lied

`test_cli_uses_enforcement_engine` was marked `@pytest.mark.asyncio` but invokes
`enforce test`, a sync Click command that calls `asyncio.run()`. Inside a running
loop that raises `RuntimeError`, the command aborted, and the test **still passed**
because its only assertion was that the engine had been *constructed*. Now runs
synchronously and asserts the command completed. Verified by injecting a crash.

### 6. runpy double-import (real CLI, not just tests)

`verdity/cli/__init__.py` eagerly imported both submodules, so
`python -m verdity.cli.review` imported the module twice. The package now re-exports
nothing. A lazy `__getattr__` was **rejected**: Python rebinds the attribute to the
*module* once the submodule is imported, making the same name resolve to a `Group`
or a `module` depending on import order.

### 7. Warnings: 15 → 0

`RuntimeWarning` / `DeprecationWarning` / `PendingDeprecationWarning` are now
promoted to errors in pytest config. 12 of the 15 came from mocking `Orchestrator`
as an `AsyncMock` when `register_specialist()` and `get_run()` are synchronous.

---

## The 3 remaining pragmas

All three state their reason inline. Nothing is excluded to reach the number.

| Location | Reason |
|---|---|
| `worker.py` `__main__` guard | module entry point |
| `gateway/app.py` `if platform == "github"` | shadowed by the dedicated `/webhooks/github` route registered earlier |
| `gateway/app.py` `else` (secret) | `platform_map` already rejected unknown values |

---

## ⚠️ Environment gotchas that cost real time

### Python 3.11 needs its own verification

**The single most important lesson from this session.** `master` CI runs a 6-job
matrix (ubuntu/windows × py3.11/3.12/3.13). The default local interpreter is 3.12,
and **every local run agreed with the passing CI jobs while 3.11 failed at
98.86%**. I reported a commit as fixed when it was not.

Root cause was upstream: on 3.11 coverage's tracer loses execution across the first
`await` inside a `BaseHTTPMiddleware`-dispatched ASGI handler
([coveragepy#1082](https://github.com/coveragepy/coveragepy/issues/1082),
[#1911](https://github.com/coveragepy/coveragepy/issues/1911)). Bisecting executed
lines proved it — line 588 recorded, line 590 not, despite the handler returning
**202** on 3.11. Ruled out first: pytest 9.0.3 vs 9.1.1, coverage 7.16.0 vs 7.16.2,
and `COVERAGE_CORE=sysmon`/`ctrace`.

Fixed by invoking both handlers **directly** (synthetic Starlette `Request`),
bypassing the middleware — rather than pragmas, which would hide them. This added
14 tests which surfaced real 3.11-only gaps: bitbucket secret lookup,
unconfigured-secret 401, invalid-JSON and normalization 400s, derived delivery IDs,
replay detection on both routes.

**A 100% gate across a matrix is only meaningful if every job is exercised
locally.** Both interpreters are now installed:

```bash
python3.11 -m pip install -e ".[dev]" --break-system-packages   # needed once
python3.11 -m pytest tests/ -q                                 # verify on 3.11
```

### Do not `git reset --hard` in this working tree

There are **30 pre-existing uncommitted changes** (28 deleted paths under
`skills/workstreams/` plus 2 modified `dev-notes` files: `GOAL.md` and
`SECURITY.md`). I destroyed them once with `reset --hard` during this session and
recovered them from a dangling stash commit.

This file (`HANDOFF.md`) is a 31st modification made by hand — it is **not** part of
that pre-existing set, and is safe to commit or discard independently.

Stash carefully: `git stash push -u` would also sweep up this file and the untracked
`dev-notes/*.md`, so restore the exact set rather than assuming.

```bash
git stash push -u -m 'preexisting-30'
git checkout -b <branch>
# ... work ...
git stash pop
```

To confirm the pre-existing set is intact, it should show 28 `D` and 2 `M`
(`dev-notes/GOAL.md`, `dev-notes/SECURITY.md`), plus this file:

```bash
git status --short | grep -c '^ D'   # 28
git status --short | grep '^ M'       # GOAL.md, HANDOFF.md, SECURITY.md
```

### `verdity.gateway.app` resolves to the FastAPI instance, not the module

`verdity/gateway/__init__.py` does `from verdity.gateway.app import app`, which
shadows the submodule of the same name. `import verdity.gateway.app as m` gives you a
`FastAPI` object, and `m.app` raises `AttributeError`.

```python
import importlib
gateway_module = importlib.import_module("verdity.gateway.app")
```

The same shadowing existed in `verdity.cli`, which is why that package re-exports
nothing now.

### AsyncMock hides non-existent methods

The single reason three MCP bugs survived a 100%-coverage suite: an `AsyncMock`
will happily invent any attribute, so a call to a method that does not exist passes
every mocked test. When mocking a collaborator, assert the method names exist on the
real class (see `TestMcpUsesRealGitHubClientApi`).

---

## Known open issues

### `coverage-gate.yml` still fails on master — pre-existing, unrelated

```yaml
pip install -e /home/dimona/.config/opencode/skills/silent-failure-hunter
```

A **hardcoded absolute path to a machine-local skill**. It can never resolve in CI and
has failed on every `master` commit, including before this work. GitHub reports it as
"likely failed because of a workflow file issue".

Fix by vendoring the tool into the repo or dropping the step. Worth its own small PR.
Note it uses `--cov-fail-under=95` while pytest uses `100`.

### PR #38 needs a rebase — commented, awaiting its author

33 commits / ~10 days stale. Conflicts in `mcp_server.py`, `test_mcp_server.py`,
`test_mcp_server_extra.py`. A naive merge would revert ~498 lines of #62's work. Its
`_models.py` `summary` field merges cleanly and is independently valuable. Analysis
posted; the author has the decision.

### Scanner matches patterns inside prose — filed as [#63](https://github.com/Dream-Pixels-Forge/verdity/issues/63)

`_VULN_PATTERNS` searches raw text without stripping comments/docstrings. On lmux PR
#35 the pattern `r"\.\./"` matched the docstring of the function that **rejects**
`../`, producing a false "medium Path Traversal" on a path-traversal *fix*. Fix
option: tokenize and blank out matched regions for `.py`, preserving line numbers.

### Other open verdity PRs (not mine, untouched)

`#36`, `#37`, `#38`, `#39`, `#59`, `#60`.

---

## Check-run posting needs GitHub App auth

A PAT gets `403 You must authenticate via a GitHub App`. Posting Verdity results as a
PR comment works; posting a check run needs org-owned App credentials.

---

## lmux (separate repo) — done

| PR | Status |
|---|---|
| [#34](https://github.com/Dream-Pixels-Forge/lmux/pull/34) rate-limiter mutex | **MERGED**, CI green |
| [#35](https://github.com/Dream-Pixels-Forge/lmux/pull/35) tar-escape rejection | **MERGED**, CI green |

`master` @ `2717706`, 4/4 CI jobs green. Both findings from the Verdity review of #35
were triaged as **false positives**, with reasoning posted on the PR.

Open: `#32` (flatpak runtime), `#33` (handoff doc).

---

## Suggested next steps

1. **Human review and merge #64.** It is green and independent.
2. **Fix `coverage-gate.yml`** — vendor `silent-failure-hunter` or drop the step.
   Small, self-contained PR.
3. **PR #38** — offer to rebase and hand-resolve, or port the `summary` field alone.
4. **File a workstream issue** for `apply_fix` (the MCP write path) if it is wanted.
5. Reconcile `--cov-fail-under`: pytest says 100, coverage-gate says 95.

---

## Verification commands

```bash
# Verdity — run BOTH interpreters
PYTHONPATH=src python3.11 -m pytest tests/ -q    # must be 100%
PYTHONPATH=src python3    -m pytest tests/ -q    # must be 100%

python3 -m ruff check src/ tests/
python3 -m ruff format --check src/ tests/
```

Expected: `1124 passed`, `100.00%`, 0 warnings, on both.
