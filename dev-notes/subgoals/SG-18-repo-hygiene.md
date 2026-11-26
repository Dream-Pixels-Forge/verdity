# SG-18 — Repo hygiene: remove machine-specific scripts and stray files

**Severity:** Low · **Size:** XS · **Depends on:** — · **Branch:** `chore/sg-18-hygiene`

## Goal
A new contributor (or the PyPI sdist) shouldn't get scripts that only work on one person's machine.

## Evidence
- `delegate_phase1.py` opens a hard-coded absolute path (`/home/dimona/Dream-Pixels-Forge/...`) and shells out to an agent. It's a one-off automation script in the repo root.
- `.prides/context/**` files reference the same absolute local paths. They're agent-session scratch notes, not project docs.
- `check_syntax.py`, `run_test.py`, `run_coverage.py` and `run_test_coverage.py` duplicate `python -m pytest` (already configured by `pytest.ini` and `pyproject.toml`).
- `dev-notes/` has 25+ phase and gate docs with overlapping content (`GOAL.md`, `GOAL-v0.4.0.md`, `GOAL-100-COVERAGE.md`, `PHASE1_COMPLETE.md`, …). It's hard to tell what's current.
- `dev-notes/subgoals/INDEX.md` links SG-01–05, 08–10 and 15, which don't have files. (This review adds the open ones; the done ones only need a note.)
- The README "Project Structure" section still says `__init__.py  # Package root, version 0.5.0`, but the package is 0.5.1.

## Plan
1. `git rm delegate_phase1.py check_syntax.py run_test.py run_coverage.py run_test_coverage.py`, and add `.prides/` to `.gitignore` (moving anything worth keeping into `dev-notes/`).
2. Check that `MANIFEST.in` doesn't ship `dev-notes/`, `.prides/` or `specs/` in the sdist (`python -m build --sdist && tar tzf dist/*.tar.gz`).
3. Move superseded dev-notes to `dev-notes/archive/` and add `dev-notes/README.md` listing which docs are current.
4. Add a test that compares `verdity.__version__`, `importlib.metadata.version` and the CHANGELOG's top entry (it may already exist in `test_version.py`; extend it to the README).

## Found during the work
- **`.github/workflows/coverage-gate.yml` has never run.** It's invalid YAML: the inline `python3 -c` blocks start at column 0, so GitHub reports the file path as the run name and every one of its 58 runs failed with no jobs, including on master. It also runs `pip install -e /home/dimona/...` and `python -m silent_failure_hunter`, a tool that isn't on PyPI, so it can't pass on a hosted runner even once parsed. Its pytest step duplicates `ci.yml`, which already enforces 100% (not 95%) on 6 OS×Python combinations. **Owner decision (2026-11-26): deleted.**
- `.pre-commit-config.yaml`: `debug-logger` isn't a valid hook ID (pre-commit aborts on it, so *no* hook ran), and `black` conflicted with the `ruff format` that CI enforces. Fixed to `debug-statements` and `ruff-format` (rev matching the installed ruff 0.16.9). The local silent-failure-hunter hook now uses the script, which takes the path from `SILENT_FAILURE_HUNTER_PATH` and prints a notice when the tool is missing. (Before, it passed silently because of `|| true`.)
- `MANIFEST.in` shipped all of `dev-notes/` in the sdist. It now ships only `dev-notes/SECURITY.md` (the README links to it).

## Definition of done
- [x] There are no absolute home paths in tracked files outside archived notes (`coverage-gate.yml` deleted).
- [x] The sdist contents have been checked: only `dev-notes/SECURITY.md` ships, and no `.prides`, `specs` or helper scripts.
- [x] Machine-specific scripts removed; stale notes in `dev-notes/archive/`; `dev-notes/README.md` added.
- [x] `test_version_sources_agree` checks pyproject, `__version__` and the CHANGELOG.

## Commit
`chore: remove machine-specific scripts, archive stale dev-notes, fix version strings`
