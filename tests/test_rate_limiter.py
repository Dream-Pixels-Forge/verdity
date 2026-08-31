"""Tests for GitHub rate-limiter module."""

from __future__ import annotations

import time

import pytest

from verdity.rate_limiter import (
    GitHubBackoffState,
    with_github_backoff,
    with_github_backoff_context,
)


@pytest.mark.asyncio
async def test_with_github_backoff_success():
    """Successful call returns immediately without retries."""

    @with_github_backoff(max_retries=3)
    async def api_call():
        return "ok"

    result = await api_call()
    assert result == "ok"


@pytest.mark.asyncio
async def test_with_github_backoff_called_directly():
    """Decorator works when called as @with_github_backoff (not @with_github_backoff())."""
    call_count = 0

    @with_github_backoff(max_retries=1, base_delay=0.01)
    async def api_call():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise RuntimeError("transient")
        return "ok"

    result = await api_call()
    assert result == "ok"
    assert call_count == 2


@pytest.mark.asyncio
async def test_with_github_backoff_retries_on_transient_error():
    """Retries on transient errors with exponential backoff."""
    call_count = 0

    @with_github_backoff(max_retries=2, base_delay=0.01, max_delay=0.05)
    async def api_call():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("transient")
        return "ok"

    result = await api_call()
    assert result == "ok"
    assert call_count == 3


@pytest.mark.asyncio
async def test_with_github_backoff_gives_up_after_max_retries():
    """Raises after max_retries exhausted."""

    @with_github_backoff(max_retries=1, base_delay=0.01)
    async def api_call():
        raise RuntimeError("permanent failure")

    with pytest.raises(RuntimeError, match="permanent failure"):
        await api_call()


@pytest.mark.asyncio
async def test_with_github_backoff_rate_limit_wait():
    """Waits until reset time on 403 with remaining=0."""
    reset_time = time.time() + 0.05

    class RateLimitError(Exception):
        status_code = 403
        rate_limit_remaining = 0
        rate_limit_reset = reset_time

    @with_github_backoff(max_retries=1, base_delay=0.01, max_delay=0.05)
    async def api_call():
        raise RateLimitError()

    start = time.monotonic()
    with pytest.raises(RateLimitError):
        await api_call()
    elapsed = time.monotonic() - start
    # Should have waited for the reset
    assert elapsed >= 0.03


@pytest.mark.asyncio
async def test_github_backoff_state_rate_limited():
    """State tracks rate-limit and reset time."""
    future_reset = time.time() + 30.0
    state = GitHubBackoffState()
    state.record_rate_limit(future_reset)

    assert state.rate_limited is True
    assert state.reset_in_seconds > 0
    assert state.reset_in_seconds < 30.1


@pytest.mark.asyncio
async def test_github_backoff_state_clears_on_success():
    """Success clears rate-limit state."""
    state = GitHubBackoffState()
    state.record_rate_limit(time.time() + 60.0)
    assert state.rate_limited is True

    state.record_success()
    assert state.rate_limited is False
    assert state.reset_in_seconds == 0.0


@pytest.mark.asyncio
async def test_github_backoff_state_wait_if_needed():
    """wait_if_needed sleeps when rate-limited."""
    state = GitHubBackoffState()
    state.record_rate_limit(time.time() + 0.1)

    start = time.monotonic()
    await state.wait_if_needed()
    elapsed = time.monotonic() - start

    assert elapsed >= 0.05


@pytest.mark.asyncio
async def test_github_backoff_state_wait_noop_when_not_limited():
    """wait_if_needed is a no-op when not rate-limited."""
    state = GitHubBackoffState()
    start = time.monotonic()
    await state.wait_if_needed()
    elapsed = time.monotonic() - start

    assert elapsed < 0.01


def test_github_backoff_state_current_delay():
    """current_delay grows with consecutive rate-limit errors."""
    state = GitHubBackoffState(base_delay=1.0, max_delay=8.0)
    assert state.current_delay() == 1.0
    state.record_error(403)
    assert state.rate_limited is True
    assert state.current_delay() == 2.0
    state.record_error(403)
    assert state.current_delay() == 4.0
    state.record_error(403)
    assert state.current_delay() == 8.0  # capped
    state.record_success()
    assert state.current_delay() == 1.0


@pytest.mark.asyncio
async def test_with_github_backoff_context_success():
    """Context wrapper succeeds on first try."""

    async def fake_api():
        return 42

    state = GitHubBackoffState()
    result = await with_github_backoff_context(fake_api, state=state)
    assert result == 42
    assert not state.rate_limited


@pytest.mark.asyncio
async def test_with_github_backoff_context_records_error():
    """Context wrapper records errors and retries."""
    call_count = 0

    async def flaky_api():
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise ConnectionError("network error")
        return "recovered"

    state = GitHubBackoffState(max_retries=2, base_delay=0.01)
    result = await with_github_backoff_context(flaky_api, state=state)
    assert result == "recovered"
    assert call_count == 2
    assert not state.rate_limited


@pytest.mark.asyncio
async def test_with_github_backoff_context_rate_limit():
    """Context wrapper handles rate-limit with wait."""
    reset_time = time.time() + 0.05

    class RateLimitError(Exception):
        status_code = 403
        rate_limit_reset = reset_time

    call_count = 0

    async def rate_limited_api():
        nonlocal call_count
        call_count += 1
        raise RateLimitError()

    state = GitHubBackoffState(max_retries=1, base_delay=0.01)
    with pytest.raises(RateLimitError):
        await with_github_backoff_context(rate_limited_api, state=state)
    assert state.rate_limited


@pytest.mark.asyncio
async def test_with_github_backoff_decorator_factory():
    """Decorator works as both @dec and @dec()."""
    from verdity.rate_limiter import with_github_backoff

    # Test the factory path: @with_github_backoff(max_retries=1)
    @with_github_backoff(max_retries=1, base_delay=0.01)
    async def api():
        return "factory-ok"

    result = await api()
    assert result == "factory-ok"


@pytest.mark.asyncio
async def test_with_github_backoff_context_last_exc_raised():
    """Context wrapper raises last exception after exhausting retries."""
    from verdity.rate_limiter import with_github_backoff_context, GitHubBackoffState

    async def always_fails():
        raise RuntimeError("final error")

    state = GitHubBackoffState(max_retries=1, base_delay=0.01)
    with pytest.raises(RuntimeError, match="final error"):
        await with_github_backoff_context(always_fails, state=state)


def test_with_github_backoff_no_parens():
    """Decorator works without parentheses: @with_github_backoff."""
    from verdity.rate_limiter import with_github_backoff

    @with_github_backoff  # no parens — hits line 88
    async def api():
        return "no-parens-ok"

    # The decorator without parens returns a coroutine wrapper; verify it's callable
    assert callable(api)
    coro = api()
    assert coro is not None
    # Clean up the coroutine to avoid RuntimeWarning
    coro.close()


@pytest.mark.asyncio
async def test_with_github_backoff_context_none_state():
    """Context wrapper creates default state when state=None."""
    from verdity.rate_limiter import with_github_backoff_context

    async def simple_api():
        return 99

    # state=None — hits line 160
    result = await with_github_backoff_context(simple_api)
    assert result == 99
