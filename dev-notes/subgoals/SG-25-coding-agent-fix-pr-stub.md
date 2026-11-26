# SG-25 — `CodingAgent.apply_fix_and_open_pr` is a stub that reports fake success

**Severity:** High (posts to the wrong PR, misreports) · **Size:** M · **Depends on:** SG-17 · **Branch:** `fix/sg-25-coding-agent-fix-pr`

## Goal
Either make "apply fix and open a PR" real, or make it honestly unavailable. Today it pretends.

## Evidence (`src/verdity/coding_agent.py`, found by SG-17's guard test)
- It reads the target file and **throws the content away** (`f.read()` with no assignment, and a bare `"\n".join(...)` expression). No file is changed, no branch is created and no commit is made.
- It calls `client.get_pr(owner, repo, pr_number=1)`. **PR #1 is hard-coded.** If that call fails, it calls `post_pr_review(..., pr_number=1, event="COMMENT")`, which comments on whatever PR #1 is in that repo.
- It builds the client with `GitHubClient(app_id=0, private_key_pem=b"", installation_id="")` and never reads `Settings`.
- On "success" it returns `commit_sha=f"verdity/{finding_id}"` and `pr_url=.../pull/{pr_number}`. Both are invented. It also passes `explanation=` and `confidence=` to `FixResult`, which has neither field, so the success path raises `TypeError` and lands in the `except`. Every call returns `success=False` with a confusing error, and the tests document this as a "defensive bug".
- It skips constraint #6 (gate → independent verifier → regression): `VerificationGate` is never invoked.
- SG-17 added the posting gate, so sub-threshold findings can't reach a PR through it any more. Everything else above still stands.

## Plan (TDD)
1. **Decide (owner):** implement it for real, or remove it and keep `propose_fix` / `generate_fix` (which return patches and are honest) as the supported surface.
2. **If implemented:**
   - Red: the PR number comes from the caller and is never defaulted. Nothing is posted unless `VerificationGate.run()` passes and the independent verifier agrees. The returned `commit_sha` and `pr_url` come from the API responses.
   - Green: add a `GitHubClient` with `create_branch`, `put_file_contents` (Contents API) and `create_pull`; build it from `Settings`; return the real URLs; add `explanation` and `confidence` to `FixResult`, or stop passing them.
3. **If removed:** delete the method and its tests, point the MCP `apply_fix` tool at `propose_fix`, and add a CHANGELOG "Removed" entry.

## Definition of done
- [ ] There's no hard-coded PR number in `src/` (`git grep -n "pr_number=1" src` is empty).
- [ ] A successful `FixResult` only ever contains identifiers returned by GitHub.

## Commit
`fix(coding-agent): real branch/commit/PR flow behind verification gate (or remove stub)`
