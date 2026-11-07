# SG-01 — Baseline: dev environment, branch hygiene, packaging

**Severity:** Low (enabler) · **Size:** S · **Depends on:** — · **Branch:** `chore/sg-01-baseline`

## Goal
Get a clean, reproducible starting point so that every later subgoal can be verified with the same commands, on a clean branch, without unrelated changes mixed in.

## Evidence
- `verdity` isn't installed in the environment: `python3 -m pytest` fails at `tests/conftest.py:14` with `ModuleNotFoundError: No module named 'verdity'`. The review had to use `PYTHONPATH=src`.
- The working tree on `chore/honest-coverage` has uncommitted site-rebrand changes (`docs/DESIGN.md`, `docs/index.html`) that have nothing to do with the branch name.
- `click` is imported by `src/verdity/cli/review.py` and `cli/enforce.py` but isn't listed in `[project].dependencies`. It only works because `uvicorn` pulls it in.
- Tracked clutter at the repo root: `pyproject.toml.bak`, `delegate_phase1.py`, `run_test.py`, `run_coverage.py`, `run_test_coverage.py`, `check_syntax.py`.
- The README repeats its tagline twice (lines 7 and 9).

## Scope
- In: venv + editable install, moving the docs rebrand out of the way, declaring `click`, README duplicate, clutter cleanup.
- Out: any behaviour change in `src/`.

## Plan
1. Create a venv and run `pip install -e ".[dev]"`. Confirm `python -m pytest` runs without `PYTHONPATH`.
2. Commit the docs rebrand on its own branch (`design/site-rebrand`), or stash it. Ask the owner which.
3. Add `click>=8.1` to `[project].dependencies`.
4. Remove the duplicated tagline from the README.
5. Clutter: **confirm with the owner before deleting anything.** Then `git rm` the files they approve, or move them to `scripts/`.
6. Record the baseline numbers (tests passed, coverage %) in `INDEX.md`.

## Definition of done
- [ ] `python -m pytest` runs from a fresh venv with no `PYTHONPATH`.
- [ ] `git status` is clean on the subgoal branch.
- [ ] `click` is declared; `pip install .` into an empty venv followed by `verdity-review --help` works.
- [ ] `ruff check src tests` and `ruff format --check src tests` pass.

## Verify
```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
ruff check src tests && ruff format --check src tests
python -m pytest
```

## Commit
`chore: declare click dependency, tidy repo root, document dev setup`
