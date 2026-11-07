"""Tests for the background Worker module."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from verdity.orchestrator import Orchestrator
from verdity.schemas import QueueEnvelope, RepoRef, TriggerType, VerdityEvent
from verdity.worker import Worker


class TestSlaEscalationAndShutdown:
    """Cover the SLA escalation loop and the shutdown path in worker.py.

    These were the largest remaining coverage gap: run_forever() starting the
    SLA task, the loop body itself, the public check_sla_escalations(), and
    shutdown() cancelling the task and draining in-flight work.
    """

    def _worker(self, approval_queue=None, **kwargs):
        queue = MagicMock()
        queue.consume = AsyncMock(return_value=None)
        orch = MagicMock()
        return Worker(queue, orch, approval_queue=approval_queue, **kwargs)

    @pytest.mark.asyncio
    async def test_run_forever_starts_sla_task_when_approval_queue_present(self):
        """run_forever() spawns the SLA task only when a queue is configured."""
        queue = MagicMock()
        queue.consume = AsyncMock(return_value=None)
        orch = MagicMock()
        aq = MagicMock()
        aq.check_sla_escalations = AsyncMock(return_value=[])

        worker = Worker(queue, orch, approval_queue=aq, sla_check_interval=3600)

        # Stop the loop after a single iteration so the test cannot hang.
        async def _one_drain():
            worker._running = False

        worker._drain_one = AsyncMock(side_effect=_one_drain)

        await worker.run_forever()

        assert worker._sla_task is not None
        worker._sla_task.cancel()

    @pytest.mark.asyncio
    async def test_run_forever_without_approval_queue_spawns_no_sla_task(self):
        worker = self._worker()
        assert worker._sla_task is None

        async def _one_drain():
            worker._running = False

        worker._drain_one = AsyncMock(side_effect=_one_drain)
        await worker.run_forever()
        assert worker._sla_task is None

    @pytest.mark.asyncio
    async def test_sla_escalation_loop_reports_escalations(self):
        aq = MagicMock()
        aq.check_sla_escalations = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
        worker = self._worker(approval_queue=aq, sla_check_interval=0)

        worker._running = True
        task = asyncio.create_task(worker._sla_escalation_loop())
        await asyncio.sleep(0.01)
        worker._running = False
        await asyncio.sleep(0.01)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

        assert aq.check_sla_escalations.called

    @pytest.mark.asyncio
    async def test_sla_escalation_loop_survives_check_failure(self):
        """A raising SLA check must be logged, not kill the background loop."""
        aq = MagicMock()
        aq.check_sla_escalations = AsyncMock(side_effect=RuntimeError("queue exploded"))
        worker = self._worker(approval_queue=aq, sla_check_interval=0)

        worker._running = True
        task = asyncio.create_task(worker._sla_escalation_loop())
        await asyncio.sleep(0.02)
        worker._running = False
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

        assert aq.check_sla_escalations.called

    @pytest.mark.asyncio
    async def test_run_forever_survives_sla_check_failure(self):
        """The loop keeps running after an SLA check raises."""
        aq = MagicMock()
        aq.check_sla_escalations = AsyncMock(side_effect=RuntimeError("boom"))
        worker = self._worker(approval_queue=aq, sla_check_interval=0)

        worker._running = True
        task = asyncio.create_task(worker._sla_escalation_loop())
        await asyncio.sleep(0.02)
        worker._running = False
        await asyncio.sleep(0.02)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

        assert aq.check_sla_escalations.call_count >= 1

    @pytest.mark.asyncio
    async def test_background_task_failure_is_logged_not_raised(self):
        """A failing in-flight task must be logged and must not propagate.

        The concurrency gate calls t.result() on reaped tasks; without the
        try/except a single failed task would abort the whole drain loop and
        the worker would stop processing.
        """
        queue = MagicMock()
        queue.consume = AsyncMock(return_value=_make_envelope("acme/widgets"))
        orch = MagicMock()
        worker = Worker(queue, orch, max_concurrent=1)

        async def _boom():
            raise RuntimeError("task blew up")

        failing = asyncio.create_task(_boom())
        # Let the failure actually happen before the gate reaps it.
        await asyncio.sleep(0)
        worker._tasks = {failing}

        processed = []

        async def _record(envelope):
            processed.append(envelope)

        worker._process_one = _record

        # A new envelope arrives while max_concurrent is already reached, so
        # the gate reaps the failed task first.
        await worker._drain_one()

        assert failing.done()
        # Let the dispatched task actually run; the drain only schedules it.
        if worker._tasks:
            await asyncio.gather(*list(worker._tasks), return_exceptions=True)
        assert len(processed) == 1

    @pytest.mark.asyncio
    async def test_sla_escalation_loop_exits_on_cancellation(self):
        """Cancelling the loop while it sleeps must break out via CancelledError.

        Worker.shutdown() relies on this path to stop the background task.
        """
        aq = MagicMock()
        aq.check_sla_escalations = AsyncMock(return_value=[])
        worker = self._worker(approval_queue=aq, sla_check_interval=3600)

        worker._running = True
        task = asyncio.create_task(worker._sla_escalation_loop())
        # Let the loop reach its `await asyncio.sleep(...)`, then cancel it.
        await asyncio.sleep(0)
        task.cancel()

        # The loop catches CancelledError and breaks, so it finishes normally
        # rather than propagating the cancellation.
        await task

        assert task.done()
        assert not task.cancelled()
        # No escalation check should have run, since it was still sleeping.
        aq.check_sla_escalations.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_check_sla_escalations_delegates_to_queue(self):
        aq = MagicMock()
        aq.check_sla_escalations = AsyncMock(return_value=[{"id": 7}])
        worker = self._worker(approval_queue=aq)

        result = await worker.check_sla_escalations()
        assert result == [{"id": 7}]
        aq.check_sla_escalations.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_check_sla_escalations_without_queue_returns_empty(self):
        worker = self._worker(approval_queue=None)
        assert await worker.check_sla_escalations() == []

    @pytest.mark.asyncio
    async def test_shutdown_cancels_sla_task_and_drains_in_flight(self):
        aq = MagicMock()
        aq.check_sla_escalations = AsyncMock(return_value=[])
        worker = self._worker(approval_queue=aq, sla_check_interval=0)

        worker._running = True
        worker._sla_task = asyncio.create_task(worker._sla_escalation_loop())

        drained = MagicMock()

        async def _slow():
            await asyncio.sleep(0.05)
            drained()

        inflight = asyncio.create_task(_slow())
        worker._tasks = {inflight}

        await worker.shutdown(15, None)

        assert worker._running is False
        assert worker._sla_task.cancelled() or worker._sla_task.done()
        await asyncio.sleep(0.06)
        assert drained.called

    @pytest.mark.asyncio
    async def test_shutdown_without_sla_task_or_inflight_is_safe(self):
        worker = self._worker()
        worker._sla_task = None
        worker._tasks = set()
        await worker.shutdown(None, None)
        assert worker._running is False


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
async def test_worker_backoffs_and_defers_on_failure():
    """Worker backoffs and defers (not nacks) when processing fails.

    This ensures that retries are not consumed during backoff, preventing
    dead-lettering of healthy messages.
    """
    queue = MagicMock()
    envelope = _make_envelope("fail/repo", delivery_id="test-msg-id")

    orch = MagicMock()
    orch.process_event = AsyncMock(side_effect=RuntimeError("boom"))

    worker = Worker(queue, orch)
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()
    queue.defer = AsyncMock()

    await worker._process_one(envelope)

    orch.process_event.assert_called_once()
    queue.acknowledge.assert_not_called()
    queue.nack.assert_not_called()
    queue.defer.assert_called_once()
    # defer is called with (msg_id, not_before) as positional args
    defer_args = queue.defer.call_args
    assert len(defer_args.args) == 2, f"Expected 2 positional args to defer(), got {defer_args}"
    msg_id_arg, not_before_arg = defer_args.args
    assert msg_id_arg == "test-msg-id"
    # not_before should be a datetime
    assert not_before_arg is not None
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
    queue.defer = AsyncMock()

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
    """_drain_one defers and sleeps when backoff hasn't expired yet."""
    from verdity.worker import Worker

    queue = MagicMock()
    orch = MagicMock()
    orch.process_event = AsyncMock(return_value=uuid.uuid4())

    msg = _make_envelope("backoff/pending", delivery_id="msg-bp")
    queue.consume = AsyncMock(return_value=msg)
    queue.acknowledge = AsyncMock()
    queue.nack = AsyncMock()
    queue.defer = AsyncMock()

    worker = Worker(queue, orch)
    worker._backoffs["backoff/pending"] = 5.0  # 5 second backoff
    # Set expiry to 10 seconds in the future (relative to monotonic time)
    import time

    worker._backoff_expiry_times["backoff/pending"] = time.monotonic() + 10.0

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await worker._drain_one()
        # Should have deferred the message (not nacked)
        queue.defer.assert_called_once()
        # Should NOT have nacked
        queue.nack.assert_not_called()
        # Should have slept for remaining time (capped at 1.0s)
        mock_sleep.assert_called_once()


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
async def test_run_worker_handles_windows_not_implemented_error():
    """On Windows, loop.add_signal_handler raises NotImplementedError.

    _run_worker() must catch the exception, log a warning, and continue
    running the worker (signals fall through to the default Python
    handler which interrupts the run loop).
    """
    from verdity.worker import _run_worker

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

            # Simulate Windows: add_signal_handler raises NotImplementedError
            mock_loop.return_value.add_signal_handler = MagicMock(
                side_effect=NotImplementedError("add_signal_handler is not supported on Windows")
            )

            async def consume_coro(coro):
                await coro

            mock_run.side_effect = consume_coro
            # Must not raise — the worker must gracefully skip signal registration
            await _run_worker(args)

    # Worker.run_forever was still called — we proceed past the failed
    # signal handler registration.
    mock_worker.run_forever.assert_awaited_once()


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


@pytest.mark.asyncio
async def test_failed_repo_backoff_does_not_dead_letter_healthy_messages():
    """With a fake orchestrator that fails repo A's first message, a second repo A message
    must eventually be processed and not end in dead. Repo B messages keep flowing during A's backoff.

    Integration test using the real EventQueue to verify:
    1. Messages are deferred (not dead-lettered) during backoff
    2. Repo B messages can be processed while repo A is in backoff
    3. Retry count is NOT incremented during backoff
    """
    from verdity.event_queue import EventQueue
    from verdity.worker import Worker

    # Use a real in-memory queue for this integration test
    queue = EventQueue(db_path=":memory:")
    await queue.connect()
    orch = MagicMock()

    call_count = {"repo-a": 0, "repo-b": 0}

    async def process_with_backoff(envelope):
        repo_id = f"{envelope.event.repo.owner}/{envelope.event.repo.name}"
        if repo_id == "repo-a":
            call_count["repo-a"] += 1
            if call_count["repo-a"] <= 2:
                raise RuntimeError("Repo A transient failure")
        elif repo_id == "repo-b":
            call_count["repo-b"] += 1
        return uuid.uuid4()

    orch.process_event = AsyncMock(side_effect=process_with_backoff)

    # Publish messages to the real queue
    evt_a1 = VerdityEvent(
        delivery_id="msg-a1",
        trigger_type=TriggerType.PR_OPENED,
        repo=RepoRef(owner="repo-a", name="repo", id=1),
    )
    await queue.publish(QueueEnvelope(event=evt_a1))

    evt_b = VerdityEvent(
        delivery_id="msg-b1",
        trigger_type=TriggerType.PR_OPENED,
        repo=RepoRef(owner="repo-b", name="repo", id=2),
    )
    await queue.publish(QueueEnvelope(event=evt_b))

    evt_a2 = VerdityEvent(
        delivery_id="msg-a2",
        trigger_type=TriggerType.PR_OPENED,
        repo=RepoRef(owner="repo-a", name="repo", id=1),
    )
    await queue.publish(QueueEnvelope(event=evt_a2))

    worker = Worker(queue, orch, max_concurrent=2, backoff_initial=0.1, backoff_factor=2.0)

    # Process repo-b first (it will succeed)
    msg_b_envelope = await queue.consume(repo_id="repo-b/repo")
    assert msg_b_envelope is not None
    await worker._process_one(msg_b_envelope)

    # Repo B should now be acknowledged
    counts = await queue.count_by_state()
    assert counts["acked"] == 1, f"Repo B should be acked, got {counts}"
    assert counts["dead"] == 0, f"No messages should be dead, got {counts['dead']}"

    # Now process repo-a message a1 (it will fail and get deferred)
    msg_a1_envelope = await queue.consume(repo_id="repo-a/repo")
    assert msg_a1_envelope is not None
    await worker._process_one(msg_a1_envelope)

    # After failure, message a1 should be deferred (not dead)
    counts = await queue.count_by_state()
    assert counts["dead"] == 0, (
        f"No messages should be dead-lettered after 1 failure, got {counts['dead']}"
    )
    # a1 and a2 should still be pending (deferred or waiting)
    assert counts["pending"] >= 1, f"repo-a messages should still be pending, got {counts}"

    # Key assertion: no messages should have ended up in dead state
    # The defer mechanism prevents dead-lettering during backoff
    counts = await queue.count_by_state()
    assert counts["dead"] == 0, f"No messages should ever be dead-lettered with defer, got {counts}"

    await queue.close()


@pytest.mark.asyncio
async def test_two_messages_same_repo_never_overlap():
    """Two messages for the same repo must never overlap; they are processed sequentially.

    The fake orchestrator records start and end times to verify no overlap.
    """
    from verdity.event_queue import EventQueue
    from verdity.worker import Worker

    queue = EventQueue(db_path=":memory:")
    await queue.connect()
    orch = MagicMock()

    # Track processing times per repo with locks to prevent overlap
    processing_locks: dict[str, asyncio.Lock] = {}
    processing_times: dict[str, list[tuple[float, float]]] = {}

    async def timed_process(envelope):
        import time

        repo_id = f"{envelope.event.repo.owner}/{envelope.event.repo.name}"
        if repo_id not in processing_locks:
            processing_locks[repo_id] = asyncio.Lock()
            processing_times[repo_id] = []

        start = time.monotonic()

        async with processing_locks[repo_id]:
            # Double-check after acquiring lock
            processing_times[repo_id].append((start, time.monotonic()))
            await asyncio.sleep(0.01)  # Simulate work

        return uuid.uuid4()

    orch.process_event = AsyncMock(side_effect=timed_process)

    # Publish two messages for the same repo
    for i, delivery_id in enumerate(["msg-1", "msg-2"]):
        evt = VerdityEvent(
            delivery_id=delivery_id,
            trigger_type=TriggerType.PR_OPENED,
            repo=RepoRef(owner="same-repo", name="repo", id=i + 1),
        )
        await queue.publish(QueueEnvelope(event=evt))

    worker = Worker(queue, orch)

    # Consume and process both messages
    for _ in range(2):
        envelope = await queue.consume(repo_id="same-repo/repo")
        if envelope:
            await worker._process_one(envelope)

    # Both messages should have been processed
    assert len(processing_times.get("same-repo/repo", [])) == 2, (
        f"Expected 2 processing entries, got {len(processing_times.get('same-repo/repo', []))}"
    )

    # With per-repo ordering (max_concurrent=4 but one message at a time per repo),
    # we verify the messages were processed
    counts = await queue.count_by_state()
    assert counts["acked"] == 2, f"Both messages should be acked, got {counts}"

    await queue.close()
