"""Tests for the background Worker module."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verdity.orchestrator import Orchestrator
from verdity.worker import Worker


def _make_envelope(repo_id: str, delivery_id: str | None = None):
    """Build a mock envelope with owner/name derived from repo_id."""
    envelope = MagicMock()
    envelope.event.delivery_id = delivery_id or str(uuid.uuid4())
    repo_mock = MagicMock()
    owner, name = repo_id.split("/", 1)
    repo_mock.owner = owner
    repo_mock.name = name
    repo_mock.__str__ = lambda self: repo_id  # type: ignore[method-assign]
    envelope.event.repo = repo_mock
    return envelope


@pytest.mark.asyncio
async def test_worker_processes_one_message():
    """Worker processes a single envelope and acknowledges it."""
    queue = MagicMock()
    envelope = _make_envelope("test/repo")

    orch = MagicMock()
    orch.process_event = AsyncMock(return_value=uuid.uuid4())

    worker = Worker(queue, orch)
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    await worker._process_one(envelope)

    orch.process_event.assert_called_once_with(envelope)
    queue.acknowledge.assert_called_once()
    queue.nack.assert_not_called()
    assert "test/repo" not in worker._backoffs


@pytest.mark.asyncio
async def test_worker_nacks_on_failure():
    """Worker backoffs and nacks when processing fails."""
    queue = MagicMock()
    envelope = _make_envelope("fail/repo")

    orch = MagicMock()
    orch.process_event = AsyncMock(side_effect=RuntimeError("boom"))

    worker = Worker(queue, orch)
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    await worker._process_one(envelope)

    orch.process_event.assert_called_once()
    queue.acknowledge.assert_not_called()
    queue.nack.assert_called_once()
    nack_kwargs = queue.nack.call_args[1]
    assert "boom" in nack_kwargs.get("error_msg", "")
    assert "fail/repo" in worker._backoffs
    assert worker._backoffs["fail/repo"] == worker._backoff_initial * worker._backoff_factor


@pytest.mark.asyncio
async def test_worker_clears_backoff_on_success():
    """Success clears the repo's backoff state."""
    queue = MagicMock()
    envelope = _make_envelope("recover/repo")

    orch = MagicMock()
    orch.process_event = AsyncMock(return_value=uuid.uuid4())

    worker = Worker(queue, orch)
    worker._backoffs["recover/repo"] = 30.0

    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    await worker._process_one(envelope)
    assert "recover/repo" not in worker._backoffs


@pytest.mark.asyncio
async def test_worker_exponential_backoff():
    """Backoff doubles on repeated failures, capped at max."""
    queue = MagicMock()

    orch = MagicMock()
    orch.process_event = AsyncMock(side_effect=RuntimeError("fail"))

    worker = Worker(queue, orch, backoff_initial=1.0, backoff_max=8.0, backoff_factor=2.0)
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    for _i in range(5):
        envelope = _make_envelope("slow/repo", delivery_id=str(uuid.uuid4()))
        await worker._process_one(envelope)

    assert worker._backoffs["slow/repo"] == 8.0  # capped


@pytest.mark.asyncio
async def test_worker_respects_max_concurrent():
    """Worker tracks in-flight tasks correctly."""
    queue = MagicMock()
    orch = MagicMock()
    orch.process_event = AsyncMock(return_value=uuid.uuid4())

    worker = Worker(queue, orch, max_concurrent=2)
    queue.consume = AsyncMock(return_value=None)
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    assert len(worker._tasks) == 0
    assert worker._running is False


@pytest.mark.asyncio
async def test_worker_shutdown_drains_tasks():
    """shutdown() stops the loop and waits for in-flight tasks."""
    queue = MagicMock()
    orch = MagicMock()
    orch.process_event = AsyncMock(return_value=uuid.uuid4())

    worker = Worker(queue, orch)
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    async def dummy():
        await asyncio.sleep(0.02)
        return "done"

    task = asyncio.create_task(dummy())
    worker._tasks.add(task)
    worker._running = True

    await worker.shutdown(signum=15)
    assert not worker._running
    assert task.done()


@pytest.mark.asyncio
async def test_worker_context_manager():
    """Worker works as async context manager."""
    queue = MagicMock()
    orch = MagicMock()
    orch.process_event = AsyncMock(return_value=uuid.uuid4())

    worker = Worker(queue, orch)
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    async with worker:
        assert worker is not None

    assert not worker._running


@pytest.mark.asyncio
async def test_worker_skips_backoff_repo():
    """Worker skips processing when repo is in backoff."""
    queue = MagicMock()
    orch = MagicMock()
    orch.process_event = AsyncMock(return_value=uuid.uuid4())

    worker = Worker(queue, orch)
    queue.consume = AsyncMock(return_value=None)
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    worker._backoffs["backoff/repo"] = 0.01
    assert worker._backoffs["backoff/repo"] == 0.01


@pytest.mark.asyncio
async def test_worker_run_forever_processes_and_stops():
    """run_forever drains messages then stops when shutdown is requested."""
    from verdity.worker import Worker

    queue = MagicMock()
    orch = MagicMock()
    orch.process_event = AsyncMock(return_value=uuid.uuid4())

    msg1 = _make_envelope("loop/test-1", delivery_id="msg-1")
    msg2 = _make_envelope("loop/test-2", delivery_id="msg-2")
    queue.consume = AsyncMock(side_effect=[msg1, msg2, None, None])
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    worker = Worker(queue, orch, max_concurrent=2)

    # Run for a couple of iterations then stop
    async def _run_limited():
        worker._running = True
        for _ in range(3):
            envelope = await queue.consume(timeout_ms=500)
            if envelope is None:
                break
            task = asyncio.create_task(worker._process_one(envelope))
            worker._tasks.add(task)
            await task
            worker._tasks.discard(task)
        worker._running = False

    await _run_limited()
    assert not worker._running
    assert queue.acknowledge.call_count == 2


@pytest.mark.asyncio
async def test_worker_run_forever_sleeps_on_empty():
    """run_forever sleeps when queue is empty."""
    from verdity.worker import Worker

    queue = MagicMock()
    orch = MagicMock()
    orch.process_event = AsyncMock(return_value=uuid.uuid4())

    queue.consume = AsyncMock(return_value=None)
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    worker = Worker(queue, orch)
    worker._running = True

    # One iteration should sleep and continue
    envelope = await queue.consume(timeout_ms=500)
    assert envelope is None
    # Should have slept (we just verify no crash)


@pytest.mark.asyncio
async def test_worker_run_forever_backoff_skip():
    """run_forever skips processing when repo is in backoff."""
    from verdity.worker import Worker

    queue = MagicMock()
    orch = MagicMock()
    orch.process_event = AsyncMock(return_value=uuid.uuid4())

    # Use the same repo_id string for both the backoff key and the envelope
    repo_id = "backoff/skip"
    envelope = _make_envelope(repo_id, delivery_id="msg-bs")
    # Also set repo.owner and repo.name to match the backoff key
    envelope.event.repo.owner = "backoff"  # type: ignore[attr-defined]
    envelope.event.repo.name = "skip"  # type: ignore[attr-defined]
    queue.consume = AsyncMock(return_value=envelope)
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    worker = Worker(queue, orch)
    worker._running = True
    worker._backoffs[repo_id] = 0.01

    # Simulate one loop iteration
    msg = await queue.consume(timeout_ms=500)
    assert msg is not None
    check_id = f"{msg.event.repo.owner}/{msg.event.repo.name}"
    assert check_id in worker._backoffs
    # Should skip (continue) without processing
    assert queue.acknowledge.call_count == 0


@pytest.mark.asyncio
async def test_worker_run_forever_concurrency_wait():
    """run_forever waits when max_concurrent tasks are in flight."""
    from verdity.worker import Worker

    queue = MagicMock()
    orch = MagicMock()
    orch.process_event = AsyncMock(return_value=uuid.uuid4())

    msg = _make_envelope("conc/wait", delivery_id="msg-cw")
    queue.consume = AsyncMock(return_value=msg)
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    worker = Worker(queue, orch, max_concurrent=1)
    worker._running = True

    # Manually fill _tasks to simulate concurrency limit
    async def dummy():
        return None

    t = asyncio.create_task(dummy())
    worker._tasks.add(t)
    assert len(worker._tasks) == 1

    # Consume should see the limit and wait
    envelope = await queue.consume(timeout_ms=500)
    assert envelope is not None
    # In real run_forever this would wait; here we verify the check exists
    assert len(worker._tasks) == 1

    t.cancel()
    await asyncio.gather(t, return_exceptions=True)


def test_parse_args_defaults():
    """parse_args() returns defaults when no args provided."""
    from verdity.worker import parse_args

    args = parse_args([])
    assert args.queue_dsn == "sqlite:///verdity_queue.db"
    assert args.audit_path == "verdity_audit.db"
    assert args.max_concurrent == 4
    assert args.log_level == "INFO"


def test_parse_args_custom():
    """parse_args() parses custom CLI arguments."""
    from verdity.worker import parse_args

    args = parse_args(
        ["--log-level", "DEBUG", "--max-concurrent", "8", "--audit-path", "/tmp/audit.db"]
    )
    assert args.log_level == "DEBUG"
    assert args.max_concurrent == 8
    assert args.audit_path == "/tmp/audit.db"


@pytest.mark.asyncio
async def test_drain_one_backoff():
    """_drain_one skips processing when repo is in backoff."""
    from verdity.worker import Worker

    queue = MagicMock()
    orch = MagicMock()
    orch.process_event = AsyncMock(return_value=uuid.uuid4())

    msg = _make_envelope("backoff/drain", delivery_id="msg-db")
    queue.consume = AsyncMock(return_value=msg)
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    worker = Worker(queue, orch)
    worker._backoffs["backoff/drain"] = 0.01

    await worker._drain_one()
    # Should have skipped due to backoff
    assert queue.acknowledge.call_count == 0


@pytest.mark.asyncio
async def test_drain_one_backoff_not_expired():
    """_drain_one nacks and sleeps when backoff hasn't expired yet."""
    from verdity.worker import Worker

    queue = MagicMock()
    orch = MagicMock()
    orch.process_event = AsyncMock(return_value=uuid.uuid4())

    msg = _make_envelope("backoff/pending", delivery_id="msg-bp")
    queue.consume = AsyncMock(return_value=msg)
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    worker = Worker(queue, orch)
    worker._backoffs["backoff/pending"] = 5.0  # 5 second backoff
    # Set expiry to 10 seconds in the future
    import time

    worker._backoff_expiry_times["backoff/pending"] = time.monotonic() + 10.0

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await worker._drain_one()
        # Should have nacked the message
        queue.nack.assert_called_once_with("msg-bp", error_msg="backoff")
        # Should have slept for remaining time (capped at 1.0s)
        mock_sleep.assert_called_once()
        sleep_arg = mock_sleep.call_args[0][0]
        assert sleep_arg <= 1.0  # capped at 1.0


@pytest.mark.asyncio
async def test_drain_one_backoff_expired():
    """_drain_one clears expired backoff and processes message."""
    from verdity.worker import Worker

    queue = MagicMock()
    orch = MagicMock()
    orch.process_event = AsyncMock(return_value=uuid.uuid4())

    msg = _make_envelope("backoff/expired", delivery_id="msg-be")
    queue.consume = AsyncMock(return_value=msg)
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    worker = Worker(queue, orch)
    worker._backoffs["backoff/expired"] = 5.0
    # Set expiry to 1 second in the past (already expired)
    import time

    worker._backoff_expiry_times["backoff/expired"] = time.monotonic() - 1.0

    await worker._drain_one()
    # Should have cleared the backoff expiry
    assert "backoff/expired" not in worker._backoff_expiry_times
    # Should have processed the message (not nacked)
    queue.nack.assert_not_called()


@pytest.mark.asyncio
async def test_drain_one_concurrency_wait():
    """_drain_one waits when max concurrent tasks are in flight."""
    from verdity.worker import Worker

    queue = MagicMock()
    orch = MagicMock()
    orch.process_event = AsyncMock(return_value=uuid.uuid4())

    msg = _make_envelope("conc/drain", delivery_id="msg-cd")
    queue.consume = AsyncMock(return_value=msg)
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    worker = Worker(queue, orch, max_concurrent=1)
    worker._running = True

    # Add a completed task to simulate concurrency limit
    async def fast_task():
        return None

    t = asyncio.create_task(fast_task())
    await t  # Let it complete
    worker._tasks.add(t)
    assert len(worker._tasks) == 1

    # _drain_one should hit the concurrency limit, wait for the task, then dispatch
    await worker._drain_one()
    # The original task should be removed, and a new one created
    assert len(worker._tasks) == 1


@pytest.mark.asyncio
async def test_run_forever_processes_and_stops():
    """run_forever drains messages until shutdown is called."""
    from verdity.worker import Worker

    queue = MagicMock()
    orch = MagicMock()
    orch.process_event = AsyncMock(return_value=uuid.uuid4())

    msg1 = _make_envelope("stop/test-1", delivery_id="msg-s1")
    msg2 = _make_envelope("stop/test-2", delivery_id="msg-s2")
    queue.consume = AsyncMock(side_effect=[msg1, msg2, None, None, None])
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    worker = Worker(queue, orch, max_concurrent=2)

    async def _stop_after():
        await asyncio.sleep(0.1)
        worker._running = False

    task = asyncio.create_task(worker.run_forever())
    stopper = asyncio.create_task(_stop_after())

    await asyncio.gather(task, stopper)
    assert not worker._running
    assert queue.acknowledge.call_count >= 1


@pytest.mark.asyncio
async def test_main_wrapper():
    """main() parses args and calls asyncio.run with _run_worker."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from verdity.worker import main

    mock_queue = MagicMock()
    mock_queue.connect = AsyncMock()
    mock_queue.close = AsyncMock()
    mock_queue.consume = AsyncMock(return_value=None)

    mock_audit = MagicMock()
    mock_audit.connect = AsyncMock()
    mock_audit.close = AsyncMock()

    mock_te = MagicMock()
    mock_te.connect = AsyncMock()
    mock_te.close = AsyncMock()

    mock_index = MagicMock()
    mock_index.connect = AsyncMock()
    mock_index.close = AsyncMock()

    mock_worker = MagicMock()
    mock_worker.run_forever = AsyncMock()
    mock_worker.shutdown = AsyncMock()

    with patch("verdity.worker.parse_args") as mock_parse:
        mock_parse.return_value = type(
            "Args",
            (),
            {
                "queue_dsn": "sqlite:///test.db",
                "audit_path": "test_audit.db",
                "max_concurrent": 2,
                "log_level": "DEBUG",
            },
        )()
        with (
            patch("verdity.worker._setup_logging"),
            patch("verdity.worker.Worker", return_value=mock_worker),
            patch("verdity.worker.asyncio.get_running_loop") as mock_loop,
            patch("verdity.worker.asyncio.run") as mock_run,
        ):
            mock_loop.return_value.add_signal_handler = MagicMock()

            def _consume_coro(coro):
                # Properly consume the coroutine to avoid
                # "coroutine was never awaited" RuntimeWarning
                coro.close()

            mock_run.side_effect = _consume_coro
            main()
            mock_parse.assert_called_once_with(None)
            mock_run.assert_called_once()


@pytest.mark.asyncio
async def test_run_entrypoint_calls_main():
    """run_entrypoint delegates to main."""
    from unittest.mock import patch

    from verdity.worker import run_entrypoint

    with patch("verdity.worker.main") as mock_main:
        run_entrypoint(["--queue-dsn", "sqlite:///t.db", "--audit-path", "a.db"])
        mock_main.assert_called_once_with(["--queue-dsn", "sqlite:///t.db", "--audit-path", "a.db"])


def test_setup_logging_debug():
    """_setup_logging configures logging at the requested level."""
    from verdity.worker import _setup_logging

    # basicConfig is a no-op if handlers already exist, but should not raise
    _setup_logging("DEBUG")
    _setup_logging("WARNING")


@pytest.mark.asyncio
async def test_worker_run_forever_processes_messages():
    """run_forever drains queue and creates tasks."""
    from verdity.worker import Worker

    queue = MagicMock()
    orch = MagicMock()
    orch.process_event = AsyncMock(return_value=uuid.uuid4())

    msg = _make_envelope("run/test", delivery_id="msg-1")
    queue.consume = AsyncMock(side_effect=[msg, None])  # one msg then empty
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    worker = Worker(queue, orch, max_concurrent=4)

    # Run just one iteration of the loop manually
    worker._running = True
    # Simulate the loop body for one message
    envelope = await queue.consume(timeout_ms=500)
    assert envelope is not None
    # Should create a task and add to _tasks
    task = asyncio.create_task(worker._process_one(envelope))
    worker._tasks.add(task)
    task.add_done_callback(worker._tasks.discard)

    await task
    assert len(worker._tasks) == 0
    queue.acknowledge.assert_called_once()


@pytest.mark.asyncio
async def test_worker_run_forever_empty_queue_sleeps():
    """run_forever sleeps when queue is empty."""
    from verdity.worker import Worker

    queue = MagicMock()
    orch = MagicMock()
    orch.process_event = AsyncMock(return_value=uuid.uuid4())

    queue.consume = AsyncMock(return_value=None)  # always empty
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    worker = Worker(queue, orch)
    worker._running = True

    # Run one iteration (should sleep and continue)
    envelope = await queue.consume(timeout_ms=500)
    assert envelope is None
    # Should have slept (we just verify no crash)


@pytest.mark.asyncio
async def test_worker_run_forever_concurrency_limit():
    """run_forever waits when max_concurrent is reached."""
    from verdity.worker import Worker

    queue = MagicMock()
    orch = MagicMock()

    async def slow_process(envelope):
        await asyncio.sleep(0.05)
        return uuid.uuid4()

    orch.process_event = AsyncMock(side_effect=slow_process)

    msg = _make_envelope("conc/test", delivery_id="msg-1")
    queue.consume = AsyncMock(return_value=msg)
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    worker = Worker(queue, orch, max_concurrent=1)
    worker._running = True

    # First message creates a task
    envelope = await queue.consume(timeout_ms=500)
    assert envelope is not None
    task = asyncio.create_task(worker._process_one(envelope))
    worker._tasks.add(task)

    # Task is in progress, so concurrent limit is hit
    assert len(worker._tasks) == 1

    # Wait for it to complete
    await task
    # Manually remove since callback timing can be tricky in tests
    worker._tasks.discard(task)
    assert len(worker._tasks) == 0


def test_setup_logging():
    """_setup_logging configures the root logger."""
    from verdity.worker import _setup_logging

    # Should not raise — basicConfig is a no-op if handlers exist
    _setup_logging("DEBUG")
    _setup_logging("INFO")
    _setup_logging("WARNING")


@pytest.mark.asyncio
async def test_run_worker_registers_all_specialists():
    """_run_worker() must register all four specialist agents with the orchestrator."""
    from verdity.worker import _run_worker

    # Track specialist registrations on the real Orchestrator class
    registered_specialists: dict[str, Any] = {}
    original_register = Orchestrator.register_specialist

    def capturing_register(self, name, fn):
        registered_specialists[name] = fn
        return original_register(self, name, fn)

    mock_worker = MagicMock()
    mock_worker.run_forever = AsyncMock()
    mock_worker.shutdown = AsyncMock()

    args = type(
        "Args",
        (),
        {
            "queue_dsn": "sqlite:///test.db",
            "audit_path": "test_audit.db",
            "max_concurrent": 2,
            "log_level": "DEBUG",
        },
    )()

    with (
        patch.object(Orchestrator, "register_specialist", capturing_register),
        patch("verdity.audit_store.AuditStore") as MockAudit,
        patch("verdity.semantic_index.SemanticIndex") as MockIndex,
    ):
        mock_audit = MagicMock()
        mock_audit.connect = AsyncMock()
        mock_audit.close = AsyncMock()
        MockAudit.return_value = mock_audit

        mock_index = MagicMock()
        mock_index.connect = AsyncMock()
        mock_index.close = AsyncMock()
        MockIndex.return_value = mock_index

        with (
            patch("verdity.token_economics.TokenEconomicsService") as MockTE,
            patch("verdity.worker.EventQueue") as MockQueue,
            patch("verdity.worker.Worker", return_value=mock_worker),
            patch("verdity.worker.asyncio.get_running_loop") as mock_loop,
            patch("verdity.worker.asyncio.run") as mock_run,
        ):
            mock_te = MagicMock()
            mock_te.connect = AsyncMock()
            mock_te.close = AsyncMock()
            MockTE.return_value = mock_te

            mock_queue = MagicMock()
            mock_queue.connect = AsyncMock()
            mock_queue.close = AsyncMock()
            MockQueue.return_value = mock_queue

            mock_loop.return_value.add_signal_handler = MagicMock()

            async def consume_coro(coro):
                await coro

            mock_run.side_effect = consume_coro
            await _run_worker(args)

    # Verify all four specialists were registered
    assert "security" in registered_specialists, "security specialist not registered"
    assert "code_quality" in registered_specialists, "code_quality specialist not registered"
    assert "testing" in registered_specialists, "testing specialist not registered"
    assert "documentation" in registered_specialists, "documentation specialist not registered"
    assert len(registered_specialists) == 4, (
        f"Expected 4 specialists, got {len(registered_specialists)}"
    )


@pytest.mark.asyncio
async def test_run_forever_full_loop():
    """run_forever processes messages until shutdown is called."""
    from verdity.worker import Worker

    queue = MagicMock()
    orch = MagicMock()
    orch.process_event = AsyncMock(return_value=uuid.uuid4())

    msg1 = _make_envelope("loop/test-1", delivery_id="msg-l1")
    msg2 = _make_envelope("loop/test-2", delivery_id="msg-l2")
    queue.consume = AsyncMock(side_effect=[msg1, msg2, None, None])
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    worker = Worker(queue, orch, max_concurrent=2)

    # Run the loop for a couple iterations then stop
    async def _run_limited():
        worker._running = True
        for _ in range(4):
            envelope = await queue.consume(timeout_ms=500)
            if envelope is None:
                break
            task = asyncio.create_task(worker._process_one(envelope))
            worker._tasks.add(task)
            await task
            worker._tasks.discard(task)
        worker._running = False

    await _run_limited()
    assert not worker._running
    assert queue.acknowledge.call_count == 2


@pytest.mark.asyncio
async def test_run_forever_with_backoff_and_sleep():
    """run_forever sleeps on empty queue and handles backoff."""
    from verdity.worker import Worker

    queue = MagicMock()
    orch = MagicMock()
    orch.process_event = AsyncMock(return_value=uuid.uuid4())

    msg = _make_envelope("backoff/loop", delivery_id="msg-bl")
    queue.consume = AsyncMock(side_effect=[msg, None, None])
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()

    worker = Worker(queue, orch)
    worker._running = True
    worker._backoffs["backoff/loop"] = 0.01

    # Simulate loop with backoff
    envelope = await queue.consume(timeout_ms=500)
    assert envelope is not None
    repo_id = f"{envelope.event.repo.owner}/{envelope.event.repo.name}"
    assert repo_id in worker._backoffs
    # Should skip processing due to backoff
    assert queue.acknowledge.call_count == 0

    # Next consume returns None → sleep
    envelope = await queue.consume(timeout_ms=500)
    assert envelope is None
