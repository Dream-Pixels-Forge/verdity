# SG-03 — Fix the `verdity-enrich` CLI (stdin handling, `--output-file`) and test it

**Severity:** Medium · **Size:** S · **Depends on:** SG-02 (shared parsing helper) · **Branch:** `fix/sg-03-cli-enrich`

## Goal
Make the documented CLI behaviour real, and take `src/verdity/cli/enrich.py` from 0% to 100% coverage.

## Evidence (`src/verdity/cli/enrich.py`)
- Lines 43–55: when neither `--findings-file` nor `--diff-file` is given, `json.load(sys.stdin)` reads all of stdin and `sys.stdin.read()` then returns `""`. The command always fails with "diff is required", even though the help text says both default to stdin.
- Line 221: `--output-file` is parsed but never used; output always goes to stdout.
- Lines 92–105: the finding parsing is duplicated from the MCP tool (SG-02).
- Line 232: a bare `except Exception` hides tracebacks. That's acceptable for a CLI, but add `--debug` to re-raise.
- Coverage: 0% (98 statements).

## Plan (TDD)
1. **Red:** `tests/test_cli_enrich.py` with a stubbed gateway, covering:
   - findings from a file, diff from stdin;
   - findings from stdin, diff from a file;
   - both from stdin, which should fail with a clear message *before* reading (exit 2) — **or** accept a single JSON document `{"findings": [...], "diff": "..."}` on stdin (pick one and document it);
   - `--output-file` writes the file and prints nothing to stdout;
   - an invalid UUID or mode exits 1;
   - `KeyboardInterrupt` exits 130.
2. **Green:** implement the chosen stdin contract, honour `--output-file`, and use the shared parsing helper from SG-02.
3. Update the module docstring and `--help` text to match.

## Definition of done
- [ ] Every documented input combination works or fails with a clear message.
- [ ] `--output-file` works.
- [ ] `cli/enrich.py` is at 100% coverage.
- [ ] The README CLI section matches the behaviour.

## Commit
`fix(cli): verdity-enrich stdin contract and --output-file; add tests`
