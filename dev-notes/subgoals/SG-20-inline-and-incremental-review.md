# SG-20 — Inline suggested changes and incremental re-review

**Type:** Feature · **Size:** L · **Depends on:** SG-14, SG-17 · **Branch:** `feat/sg-20-inline-incremental`

## Why
Two things decide whether developers keep a review bot installed: **is the feedback where I'm looking** (on the line, with a one-click fix), and **does it repeat itself on every push**. Verdity does neither yet:
- Output is a check run with annotations only. `suggested_fix_diff` is shown as text inside the annotation message (`github_client.py:63–70`) or in a fenced `diff` block, not as a GitHub ```` ```suggestion ```` block a developer can apply in one click.
- `pr.synchronize` re-reviews the **whole** PR. Nothing records the last reviewed SHA, so every push re-posts every finding, and findings that were fixed are never resolved.

## Scope
1. **Finding fingerprint:** a stable hash of `(rule_id, file_path, normalised code snippet ± 2 lines)`, independent of line numbers. Store it per PR in a `pr_review_state` table (`repo, pr, last_reviewed_sha, fingerprints[]`).
2. **Incremental mode:** on `synchronize`, diff `last_reviewed_sha..head_sha` (GitHub compare API) and run specialists only over the changed hunks. Keep the full PR as *context* through the semantic index, but only report findings in new hunks.
3. **Lifecycle:**
   - A new fingerprint is posted.
   - A fingerprint that's still present is left alone (not re-posted).
   - A fingerprint that's gone has its thread **resolved** with "✅ Fixed in `abc123`". That's a visible reward for fixing things.
4. **Inline review comments:** for AUTO_APPROVE findings with a deterministic fix from `CodingAgent` that passed `VerificationGate`, post a PR review comment with a ```` ```suggestion ```` block. The check run stays as the summary and merge gate.
5. **Summary comment:** one sticky comment per PR, edited in place: counts, "held for review: N" (SG-19 link) and cost (token economics).

## Plan (TDD)
- Red: same finding, line moved by +10 → same fingerprint, not re-posted.
- Red: fix commit removes the finding → resolve call issued once.
- Red: suggestion body is exactly the replacement lines, only when the verification gate passed.
- Red: force-push (`before` not an ancestor) → fall back to a full review.
- Green: add `review_state.py` (store + fingerprinting), `PlatformAdapter.publish_inline / resolve_thread`, GitHub first.

## Definition of done
- [ ] A second push with no new issues produces **zero** new comments (asserted in the e2e test from SG-14).
- [ ] A one-click suggestion has been verified on a sandbox repo (screenshot in the PR).

## Commit
`feat(review): fingerprinted incremental re-review, inline suggestion blocks, auto-resolve fixed`
