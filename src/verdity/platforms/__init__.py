"""
Multi-platform webhook support — GitHub, GitLab, Bitbucket.

Each platform implements the abstract Platform interface:
  - verify_webhook: platform-native verification
  - normalize_event: convert platform payload to Verdity internal event
  - post_comment: post review comment on PR/MR
  - post_inline_comment: post inline (line-level) comment

Usage:
    from verdity.platforms import GitHubPlatform, GitLabPlatform, BitbucketPlatform

    platform = GitLabPlatform()
    if platform.verify_webhook(headers, body, secret):
        event = platform.normalize_event(headers, payload)
"""

from verdity.platforms.base import Platform
from verdity.platforms.bitbucket import BitbucketPlatform
from verdity.platforms.github import GitHubPlatform
from verdity.platforms.gitlab import GitLabPlatform

__all__ = [
    "BitbucketPlatform",
    "GitHubPlatform",
    "GitLabPlatform",
    "Platform",
]
