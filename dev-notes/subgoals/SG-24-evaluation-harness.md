# SG-24 — Evaluation harness: measure precision before claiming it

**Type:** Feature · **Size:** M · **Depends on:** SG-17 (the gate is what we measure) · **Branch:** `feat/sg-24-eval-harness`

## Why
Verdity's identity is *calibrated* verdicts. The router's thresholds (0.9 / 0.6) and `DEFAULT_SEVERITY_WEIGHTS` / `DEFAULT_CONCERN_BOOST` (`router.py`) are hand-picked, and `trust_calibration.py` computes precision@0.9 only from runtime signals that barely exist yet (see SG-19). There's no offline benchmark, so a change to any agent rule or weight can silently trade precision for recall, and 100% line coverage won't catch it.

## Scope
1. **Corpus** `eval/cases/<id>/`: `diff.patch`, `meta.yml` (language, tier) and `expected.yml` (findings that **must** be reported, each with a `rule_id`/`concern` and file:line ± tolerance, plus a list of `must_not` false-positive traps). Start with about 40 cases:
   - ~15 security (real secret shapes, SQL injection, path traversal, unsafe deserialisation, *and* look-alikes such as test fixtures, `EXAMPLE` keys and hashed values);
   - ~10 testing gaps, ~10 docs/breaking-change, ~5 clean PRs (expected: zero posted findings).
2. **Runner** `verdity-eval run [--tier] [--specialist] [--json]`: runs the real `gate_findings` pipeline offline (no network, LLM disabled by default) and reports per concern:
   - precision / recall of **posted** findings (what developers see);
   - precision of **queued** findings (what reviewers see);
   - a reliability table: predicted confidence bucket vs. actual hit rate (Expected Calibration Error);
   - noise per PR (posted findings on the clean cases).
3. **CI gate:** `eval/baseline.json` is committed. CI fails if posted precision drops by more than 2 pts or clean-PR noise rises above 0. Changing the baseline needs an explicit commit (like a snapshot test).
4. **Calibration bootstrap:** `verdity-eval fit-weights` proposes new severity and concern weights from the corpus within the bounds `TrustCalibrator` already enforces. It writes a diff for a human to review and never auto-applies.

## Plan (TDD)
- Red: the runner's scoring on a synthetic 3-case corpus (known TP/FP/FN) gives exact precision, recall and ECE values.
- Red: the CI comparison flags a 3-pt precision regression and passes a 1-pt one.
- Green: `src/verdity/eval/` package + `[project.scripts] verdity-eval`.

## Definition of done
- [ ] The first baseline is committed, and the README badge or section quotes the real numbers ("posted precision on Verdity-Eval v1: X%").
- [ ] The CI job runs in under 60s.

## Commit
`feat(eval): labelled PR corpus, precision/recall/ECE runner, CI regression gate`
