# SG-10 — Check and fix GitLab merge-request field mapping against a real payload

**Severity:** High if confirmed (every real GitLab MR may get 400) · **Size:** S · **Depends on:** SG-09 · **Branch:** `fix/sg-10-gitlab-fields`

## Goal
Parse real GitLab merge-request webhooks into a valid `VerdityEvent`.

## Evidence (plausible, **check first**)
- `src/verdity/platforms/gitlab.py:130–131` reads `object_attributes.head_commit_sha` and `target_commit_sha`. As far as I know, GitLab's MR webhook has `object_attributes.last_commit.id` (head) and no target SHA field (the base SHA comes from the API's `diff_refs.base_sha`, not the webhook).
- If the SHA is empty, `_sanitize_path("")` fails `^[\w\-./]+$` (needs at least 1 character), which gives **400 "Invalid PR reference"**.
- `repo.owner = project.namespace`, which is a display name and can contain spaces. `project.path_with_namespace` is the stable id.
- `diff_url` comes from `diff_refs.base.url`, which doesn't look like a real GitLab structure.

## Plan (TDD)
1. Get an authentic sample: GitLab docs "Webhook events → Merge request events", or a test hook from a real project. Save it to `tests/fixtures/gitlab_mr_open.json` with any secrets removed.
2. **Red:** posting the fixture to `/verdity/webhooks/gitlab` with a valid token returns 202, with `head_sha == last_commit.id`, owner and name from `path_with_namespace`, and `number == iid`.
3. **Green:** fix the mapping. If the base SHA isn't in the payload, allow an empty base SHA through sanitization (skip `_sanitize_path` for empty values) and resolve it later in SG-14 when the diff is fetched.
4. Do the same check for Bitbucket (`pullrequest.source.commit.hash`, `destination.commit.hash`) with a fixture.

## Definition of done
- [ ] Real-payload fixtures exist for GitLab and Bitbucket and pass end-to-end through the gateway.
- [ ] If the evidence turns out to be wrong, the fixtures and tests still go in, and the finding is closed as "not a bug".

## Commit
`fix(gitlab): map MR webhook fields from real payload (last_commit.id, path_with_namespace)`
