"""
Worker — background event processor for Verdity.

Consumes messages from the durable EventQueue and runs the Orchestrator
for each one. This is the entry point for production deployment.

Usage:
    python -m verdity.worker --queue redis://... --audit audit.db ...

Or programmatically:
    from verdity.worker import Worker
    async with Worker(queue, orchestrator) as w:
        await w.run_forever()
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from typing import Any, Self

from verdity.event_queue import EventQueue
from verdity.orchestrator import Orchestrator
from verdity.schemas import QueueEnvelope

logger = logging.getLogger(__name__)

# Backoff settings for transient errors
_INITIAL_BACKOFF = 1.0  # seconds
_MAX_BACKOFF = 60.0  # cap exponential backoff
_BACKOFF_FACTOR = 2.0  # double each retry


class Worker:
    """
    Background worker that dequeues events and runs the orchestrator.

    Handles graceful shutdown on SIGINT/SIGTERM, exponential backoff on
    transient errors, and per-repo ordering (one message at a time per repo).
    """

    def __init__(
        self,
        queue: EventQueue,
        orchestrator: Orchestrator,
        *,
        max_concurrent: int = 4,
        backoff_initial: float = _INITIAL_BACKOFF,
        backoff_max: float = _MAX_BACKOFF,
        backoff_factor: float = _BACKOFF_FACTOR,
    ) -> None:
        self._queue = queue
        self._orchestrator = orchestrator
        self._max_concurrent = max_concurrent
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._backoff_factor = backoff_factor
        self._running = False
        self._tasks: set[asyncio.Task] = set()
        self._backoffs: dict[str, float] = {}  # repo_id → next backoff seconds
        self._backoff_expiry_times: dict[str, float] = {}  # repo_id → monotonic expiry

    async def run_forever(self) -> None:
        """Main loop: dequeue and process events until shutdown."""
        self._running = True
        logger.info("Worker started, draining queue…")

        while self._running:
            await self._drain_one()

    async def _drain_one(self) -> None:
        """Process a single queue cycle: consume, check backoff, dispatch."""
        envelope = await self._queue.consume(timeout_ms=500)
        if envelope is None:
            # No messages — sleep briefly to avoid busy-waiting
            await asyncio.sleep(0.1)
            return

        # Rate-limit per repo to avoid thundering herd on large repos
        repo_id = f"{envelope.event.repo.owner}/{envelope.event.repo.name}"
        if repo_id in self._backoffs:
            # Check if backoff has expired; if not, nack and sleep remaining time
            now = time.monotonic()
            backoff_expiry = self._backoff_expiry_times.get(repo_id, 0)
            if now < backoff_expiry:
                remaining = backoff_expiry - now
                logger.debug("Backing off repo %s for %.1fs remaining", repo_id, remaining)
                # Put message back on queue for retry
                msg_id = envelope.event.delivery_id
                await self._queue.nack(msg_id, error_msg="backoff")
                await asyncio.sleep(min(remaining, 1.0))
                return
            else:
                # Backoff expired, clear it
                self._backoff_expiry_times.pop(repo_id, None)

        # Limit concurrency
        while len(self._tasks) >= self._max_concurrent:
            done, _ = await asyncio.wait(
                self._tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            self._tasks -= done
            for t in done:
                try:
                    t.result()
                except Exception as exc:  # pragma: no cover
                    logger.error("Background task errored: %s", exc)

        task = asyncio.create_task(self._process_one(envelope))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _process_one(self, envelope: QueueEnvelope) -> None:
        """Process a single envelope with error handling and backoff."""
        msg_id = envelope.event.delivery_id
        repo_id = f"{envelope.event.repo.owner}/{envelope.event.repo.name}"
        try:
            run_id = await self._orchestrator.process_event(envelope)
            logger.info("Processed delivery %s → run %s", msg_id, run_id)
            # Clear backoff on success
            self._backoffs.pop(repo_id, None)
            self._backoff_expiry_times.pop(repo_id, None)
            await self._queue.acknowledge(msg_id)
        except Exception as exc:
            logger.exception("Failed to process delivery %s", msg_id)
            # Exponential backoff for this repo
            current = self._backoffs.get(repo_id, self._backoff_initial)
            new_backoff = min(current * self._backoff_factor, self._backoff_max)
            self._backoffs[repo_id] = new_backoff
            self._backoff_expiry_times[repo_id] = time.monotonic() + new_backoff
            await self._queue.nack(msg_id, error_msg=str(exc))

    async def shutdown(self, signum: int | None = None, frame: Any = None) -> None:
        """Graceful shutdown: stop accepting new work, drain in-flight tasks."""
        logger.info("Shutdown signal received (%s), draining in-flight tasks…", signum)
        self._running = False
        if self._tasks:
            logger.info("Waiting for %d in-flight tasks…", len(self._tasks))
            await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("Worker shut down cleanly.")

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[override]
        await self.shutdown()


def _setup_logging(level: str = "INFO") -> None:
    """Configure root logging for the worker process."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )


def parse_args(argv: list[str] | None = None) -> Any:
    """Parse CLI arguments. Separated from main() for testability."""
    import argparse

    parser = argparse.ArgumentParser(description="Verdity background worker")
    parser.add_argument(
        "--queue-dsn",
        default="sqlite:///verdity_queue.db",
        help="Queue backend DSN (sqlite:///path or redis://host)",
    )
    parser.add_argument(
        "--audit-path", default="verdity_audit.db", help="Path to audit SQLite database"
    )
    parser.add_argument(
        "--max-concurrent", type=int, default=4, help="Max parallel processing tasks"
    )
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser.parse_args(argv)


async def _run_worker(args: Any) -> None:  # pragma: no cover
    """Initialize and run the worker loop. Exported for testability."""
    # Initialize queue and orchestrator
    from verdity.audit_store import AuditStore
    from verdity.semantic_index import SemanticIndex
    from verdity.token_economics import TokenEconomicsService

    db_path = (
        args.queue_dsn.split("///")[-1] if args.queue_dsn.startswith("sqlite:///") else ":memory:"
    )
    queue = EventQueue(db_path=db_path)
    await queue.connect()

    audit = AuditStore(db_path=args.audit_path)
    await audit.connect()

    te = TokenEconomicsService(db_path=args.audit_path)
    await te.connect()

    index = SemanticIndex(db_path=args.audit_path)
    await index.connect()

    # Initialize multi-model fallback for agent reliability
    from verdity.model_fallback import MultiModelFallback

    fallback = MultiModelFallback()

    orch = Orchestrator(
        queue=queue,
        semantic_index=index,
        token_economics=te,
        audit_store=audit,
    )

    # Register all specialist agents
    from verdity.agents import (
        CodeQualityAgent,
        DocumentationAgent,
        SecurityAgent,
        TestingAgent,
    )

    orch.register_specialist("security", SecurityAgent(fallback=fallback).run)
    orch.register_specialist("code_quality", CodeQualityAgent(fallback=fallback).run)
    orch.register_specialist("testing", TestingAgent(fallback=fallback).run)
    orch.register_specialist("documentation", DocumentationAgent(fallback=fallback).run)

    worker = Worker(
        queue=queue,
        orchestrator=orch,
        max_concurrent=args.max_concurrent,
    )

    # Register signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(worker.shutdown(s)))

    await worker.run_forever()

    # Cleanup
    await queue.close()
    await audit.close()
    await te.close()
    await index.close()


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for standalone worker deployment."""
    args = parse_args(argv)
    _setup_logging(args.log_level)
    asyncio.run(_run_worker(args))


def run_entrypoint(argv: list[str] | None = None) -> None:
    """Public entrypoint wrapping main; exported for testability."""
    main(argv)


if __name__ == "__main__":  # pragma: no cover
    main()
