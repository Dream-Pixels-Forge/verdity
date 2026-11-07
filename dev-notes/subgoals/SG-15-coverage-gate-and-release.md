# SG-15 — Restore the 100% coverage gate, run the final checks, prepare the release

**Severity:** Medium (the gate is red today) · **Size:** S · **Depends on:** SG-01–SG-14 · **Branch:** `chore/sg-15-coverage-release`

## Goal
`pytest` passes its own configured gate (`--cov-fail-under=100`), and the fixes ship as a properly verified patch release.

## Evidence
- Baseline: 1261 passed, **97.63%** coverage, so the configured gate fails.
- Remaining gaps after SG-02 and SG-03: `src/verdity/trust_calibration.py` lines 580–585, 637–639, 644, 669–670, 675 and 683 (LLM-assisted calibrator).
- Stray `*.py,cover` annotate files in `src/verdity/` (git-ignored). Make sure they can't end up in the sdist (`MANIFEST.in`).

## Plan
1. Write tests (no pragmas) for the `trust_calibration.py` branches: LLM unavailable, malformed suggestion JSON, suggestion outside allowed bounds, and so on.
2. Run the full gate. Fix anything that regressed.
3. Build the sdist and wheel; inspect that no `,cover`, `.db` or `.bak` files are included.
4. Bump the version (`0.5.1`) in `pyproject.toml` and `_version.py`, add a CHANGELOG entry listing SG-02 to SG-14, and update `tests/test_phase8.py`'s version whitelist.
5. Release with the AGENTS.md three-way check: code version = latest git tag = GitHub release = PyPI. Also download the version-specific artifact and check its contents.

## Definition of done
- [ ] `python -m pytest` exits 0 with 100% coverage and no new `pragma: no cover`.
- [ ] `ruff check` and `ruff format --check` are clean; CI is green on the PR.
- [ ] The artifact has been inspected; the three-way release check shows "ALL MATCH".

## Verify
```bash
ruff check src tests && ruff format --check src tests
python -m pytest
python -m build && tar tzf dist/verdity-0.5.1.tar.gz | grep -E ',cover|\.db|\.bak' && echo "LEAK" || echo "clean"
```

## Commit
`chore: restore 100% coverage gate; release 0.5.1`
