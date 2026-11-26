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

## Definition of done
- [ ] There are no absolute home paths in tracked files (`git grep -n "/home/"` is empty outside archived notes).
- [ ] The sdist contents have been checked.

## Commit
`chore: remove machine-specific scripts, archive stale dev-notes, fix version strings`
