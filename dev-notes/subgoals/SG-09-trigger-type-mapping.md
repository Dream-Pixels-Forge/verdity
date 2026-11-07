# SG-09 — Stop treating unknown, closed and merged events as "PR opened"

**Severity:** High (closing or merging a PR starts a full, billed review) · **Size:** S · **Depends on:** SG-08 · **Branch:** `fix/sg-09-trigger-mapping`

## Goal
Only events Verdity actually handles get enqueued. Everything else is acknowledged and ignored.

## Evidence
- `src/verdity/gateway/app.py`, `handle_platform_webhook`: `except ValueError: trigger_type = TriggerType.PR_OPENED  # default for unknown`.
- GitLab and Bitbucket map to `pr.closed`, `pr.merged` and `pr.approved`, which aren't in `TriggerType` (`schemas/_models.py:21`). So close, merge and approve all become `PR_OPENED`.
- Non-merge-request and non-PR events return `trigger_type: "unknown"` with no `pull_request`, so they're enqueued as PR #0 with an empty owner.
- The GitHub normalizer has its own fallback ("Normalize unknown events gracefully — fall back to a generic trigger", `webhook_normalizer.py` around line 74). Check that it behaves the same way.

## Plan (TDD)
1. **Red:** for GitLab `close`/`merge`, Bitbucket `pullrequest:fulfilled`/`declined`/`approved`, a GitLab `push` and a Bitbucket `repo:push`: expect **202 `{"status": "ignored"}`**, nothing published, and an audit row `webhook.ignored`.
2. **Red:** the GitHub route sends unsupported events (for example `issues`) to the same "ignored" path.
3. **Green:**
   - Add one set of reviewable triggers (opened, synchronize, reopened, ready_for_review, rerequested).
   - Anything that isn't in the enum, or isn't reviewable, is ignored rather than defaulted.
   - Decide (owner) whether to add `PR_CLOSED`/`PR_MERGED` to the enum for metrics, or just ignore them.
   - Bitbucket "merged" is actually `pullrequest:fulfilled`; fix the map.
4. Make sure ignored events still count toward dedup (SG-07 ordering: record after the decision).

## Definition of done
- [ ] No code path defaults an unknown trigger to `PR_OPENED` (`grep -n "PR_OPENED" src/verdity/gateway`).
- [ ] Each platform has an "ignored" test.

## Commit
`fix(gateway): ignore non-reviewable events instead of defaulting to pr.opened`
