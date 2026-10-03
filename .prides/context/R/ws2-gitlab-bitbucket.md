# Workstream 2: GitLab & Bitbucket Platform Support

## Task
Implement GitLab and Bitbucket platform adapters for webhook handling, API integration, and PR/MR comments.

## Current State
- `src/verdity/platforms/github.py` - Fully implemented
- `src/verdity/platforms/gitlab.py` - Stub only (41 lines, 0% coverage)
- `src/verdity/platforms/bitbucket.py` - Stub only (43 lines, 0% coverage)
- `src/verdity/platforms/base.py` - Base class (15 lines, 0% coverage)
- `src/verdity/platforms/__init__.py` - Exports (5 lines, 0% coverage)

## Required Implementation

### GitLab Platform Adapter (`src/verdity/platforms/gitlab.py`)
```python
class GitLabPlatform(Platform):
    async def handle_webhook(self, request: Request) -> WebhookEvent
    async def post_comment(self, project_id: str, mr_iid: int, body: str) -> dict
    async def get_merge_request(self, project_id: str, mr_iid: int) -> dict
    async def get_diff(self, project_id: str, mr_iid: int) -> str
    async def get_file_content(self, project_id: str, file_path: str, ref: str) -> str
```

### Bitbucket Platform Adapter (`src/verdity/platforms/bitbucket.py`)
```python
class BitbucketPlatform(Platform):
    async def handle_webhook(self, request: Request) -> WebhookEvent
    async def post_comment(self, workspace: str, repo_slug: str, pr_id: int, body: str) -> dict
    async def get_pull_request(self, workspace: str, repo_slug: str, pr_id: int) -> dict
    async def get_diff(self, workspace: str, repo_slug: str, pr_id: int) -> str
    async def get_file_content(self, workspace: str, repo_slug: str, file_path: str, commit: str) -> str
```

### Shared Platform Abstraction (`src/verdity/platforms/base.py`)
- Abstract base class with common interface
- Webhook signature verification abstraction
- Rate limiting integration
- Error handling standardization

## Webhook Events to Handle
- GitLab: Merge Request events (opened, updated, merged, closed)
- Bitbucket: Pull Request events (created, updated, merged, declined)

## Testing Requirements
- Unit tests for each platform adapter
- Webhook signature verification tests
- Mock HTTP client tests
- Integration tests with mock servers

## Files to Create/Modify
- `src/verdity/platforms/gitlab.py` - Full implementation
- `src/verdity/platforms/bitbucket.py` - Full implementation
- `src/verdity/platforms/base.py` - Enhance base class
- `src/verdity/platforms/__init__.py` - Export new platforms
- `tests/test_platforms_gitlab.py` - New test file
- `tests/test_platforms_bitbucket.py` - New test file