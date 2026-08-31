"""
GitHub API Rate-Limit Handling.

Provides exponential-backoff wrappers for GitHub API calls that respect
the X-RateLimit-Remaining and X-RateLimit-Reset headers.

Usage:
    @with_github_backoff(max_retries=3)
    async def post_comment(repo, number, body):
        ...

Or as a context manager:
    async with github_backoff_context() as state:
        if state.rate_limited:
            await asyncio.sleep(state.reset_in_seconds)
        # make API call...
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Default backoff settings
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BASE_DELAY = 1.0  # seconds
_DEFAULT_MAX_DELAY = 60.0  # seconds


class GitHubRateLimitError(Exception):
    """Raised when the GitHub API rate limit is exhausted and cannot recover."""


def with_github_backoff(
    func: Callable | None = None,
    *,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    base_delay: float = _DEFAULT_BASE_DELAY,
    max_delay: float = _DEFAULT_MAX_DELAY,
) -> Callable:
    """
    Decorator that retries GitHub API calls with exponential backoff.

    Handles 403 responses with X-RateLimit-Remaining: 0 by waiting until
    the reset time. Other 4xx/5xx errors are retried up to max_retries.
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            delay = base_delay
            for attempt in range(max_retries + 1):
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    status_code = getattr(exc, "status_code", None)
                    rate_limit_remaining = getattr(exc, "rate_limit_remaining", None)

                    if status_code == 403 and rate_limit_remaining == 0:
                        reset_at = getattr(exc, "rate_limit_reset", 0.0)
                        if reset_at > 0:
                            wait = max(reset_at - time.time(), 1.0)
                            logger.warning(
                                "GitHub rate limit hit — waiting %.0fs until reset", wait
                            )
                            await asyncio.sleep(min(wait, max_delay))
                            delay = base_delay  # reset delay after waiting
                            continue

                    if attempt < max_retries:
                        logger.warning(
                            "GitHub API error (attempt %d/%d): %s — retrying in %.1fs",
                            attempt + 1,
                            max_retries,
                            exc,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, max_delay)
                    else:
                        logger.error(
                            "GitHub API permanently failed after %d retries: %s", max_retries, exc
                        )
            raise last_exc  # type: ignore[misc]

        return wrapper

    if func is not None:
        return decorator(func)
    return decorator


class GitHubBackoffState:
    """Tracks rate-limit state across multiple API calls in a session."""

    def __init__(
        self,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        base_delay: float = _DEFAULT_BASE_DELAY,
        max_delay: float = _DEFAULT_MAX_DELAY,
    ) -> None:
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.rate_limited = False
        self.reset_at: float = 0.0
        self._consecutive_errors = 0

    @property
    def reset_in_seconds(self) -> float:
        """Seconds until the rate limit resets (0 if not rate-limited)."""
        if self.reset_at > 0:
            return max(self.reset_at - time.time(), 0.0)
        return 0.0

    def record_rate_limit(self, reset_at: float) -> None:
        """Called when a 403 with remaining=0 is received."""
        self.rate_limited = True
        self.reset_at = reset_at
        self._consecutive_errors = 0
        logger.warning("GitHub rate limit hit — resets in %.1fs", self.reset_in_seconds)

    def record_success(self) -> None:
        """Called on a successful API response."""
        self.rate_limited = False
        self.reset_at = 0.0
        self._consecutive_errors = 0

    def record_error(self, status_code: int | None = None) -> None:
        """Called on an API error."""
        self._consecutive_errors += 1
        if status_code == 403:
            self.rate_limited = True

    def current_delay(self) -> float:
        """Return the next backoff delay."""
        if self.rate_limited:
            return min(self.base_delay * (2**self._consecutive_errors), self.max_delay)
        return self.base_delay

    async def wait_if_needed(self) -> None:
        """Sleep if currently rate-limited, otherwise no-op."""
        if self.rate_limited and self.reset_in_seconds > 0:
            await asyncio.sleep(min(self.reset_in_seconds, self.max_delay))


async def with_github_backoff_context(
    fn: Callable,
    *args: Any,
    state: GitHubBackoffState | None = None,
    **kwargs: Any,
) -> Any:
    """
    Context-manager-style wrapper for a single GitHub API call.

    Usage:
        state = GitHubBackoffState()
        result = await with_github_backoff_context(my_api_call, state=state, repo="x")
    """
    if state is None:
        state = GitHubBackoffState()

    last_exc: Exception | None = None
    delay = state.base_delay

    for attempt in range(state.max_retries + 1):
        try:
            result = await fn(*args, **kwargs)
            state.record_success()
            return result
        except Exception as exc:
            last_exc = exc
            status_code = getattr(exc, "status_code", None)
            state.record_error(status_code)

            if status_code == 403:
                reset_at = getattr(exc, "rate_limit_reset", 0.0)
                if reset_at > 0:
                    state.record_rate_limit(reset_at)
                    await state.wait_if_needed()
                    delay = state.base_delay
                    continue

            if attempt < state.max_retries:
                logger.warning(
                    "GitHub API error (attempt %d/%d): %s", attempt + 1, state.max_retries, exc
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, state.max_delay)
            else:
                logger.error("GitHub API permanently failed after %d retries", state.max_retries)

    raise last_exc  # type: ignore[misc]
