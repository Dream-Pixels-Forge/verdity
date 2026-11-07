"""
Tests for the durable Event Queue.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from verdity.event_queue import EventQueue
from verdity.schemas import QueueEnvelope, RepoRef, TriggerType, VerdityEvent


@pytest.fixture
def sample_event():
    return VerdityEvent(
        delivery_id="del-001",
        trigger_type=TriggerType.PR_OPENED,
        repo=RepoRef(owner="acme", name="widgets", id=123),
    )


@pytest.mark.asyncio
async def test_publish_and_consume(queue: EventQueue, sample_event):
    envelope = QueueEnvelope(event=sample_event)
    msg_id = await queue.publish(envelope)
    assert isinstance(msg_id, str) and len(msg_id) > 0

    consumed = await queue.consume(repo_id="acme/widgets")
    assert consumed is not None
    assert consumed.event.delivery_id == "del-001"


@pytest.mark.asyncio
async def test_consume_returns_none_when_empty(queue: EventQueue):
    result = await queue.consume()
    assert result is None


@pytest.mark.asyncio
async def test_ack(queue: EventQueue, sample_event):
    envelope = QueueEnvelope(event=sample_event)
    msg_id = await queue.publish(envelope)
    consumed = await queue.consume()
    assert consumed is not None
    await queue.acknowledge(msg_id)

    counts = await queue.count_by_state()
    assert counts["acked"] == 1
    assert counts["pending"] == 0


@pytest.mark.asyncio
async def test_nack_and_retry(queue: EventQueue, sample_event):
    envelope = QueueEnvelope(event=sample_event)
    msg_id = await queue.publish(envelope)
    await queue.consume()
    await queue.nack(msg_id, error_msg="temp-failure")

    counts = await queue.count_by_state()
    assert counts["pending"] == 1  # retried

    # consume again and permanently fail
    await queue.consume()
    await queue.nack(msg_id, error_msg="perm-failure", max_retries=1)
    counts = await queue.count_by_state()
    assert counts["dead"] == 1


@pytest.mark.asyncio
async def test_nack_ignores_unknown_message_id(queue: EventQueue):
    """nack() should silently return when message_id is not found (no-op)."""
    await queue.nack("unknown-msg-id", error_msg="fail")
    # Should not raise; message simply not found
    counts = await queue.count_by_state()
    assert counts["pending"] == 0
    assert counts["dead"] == 0


@pytest.mark.asyncio
async def test_count_by_state_with_repo_id_filter(queue: EventQueue, sample_event):
    """count_by_state() should filter by repo_id when provided."""
    envelope = QueueEnvelope(event=sample_event)
    await queue.publish(envelope)

    counts = await queue.count_by_state(repo_id="acme/widgets")
    assert counts["pending"] == 1

    # Filter for non-existent repo should return all zeros
    counts = await queue.count_by_state(repo_id="other/repo")
    assert counts["pending"] == 0


@pytest.mark.asyncio
async def test_idempotent_publish(queue: EventQueue, sample_event):
    envelope = QueueEnvelope(event=sample_event)
    msg_id_1 = await queue.publish(envelope)
    msg_id_2 = await queue.publish(envelope)
    # Both should succeed; second is a no-op (INSERT OR IGNORE).
    # msg_ids are independently generated UUIDs — they won't match,
    # but only one row should exist in the queue.
    assert isinstance(msg_id_1, str) and len(msg_id_1) > 0
    assert isinstance(msg_id_2, str) and len(msg_id_2) > 0
    counts = await queue.count_by_state()
    assert counts["pending"] == 1  # only one message in queue


@pytest.mark.asyncio
async def test_queue_stats(queue: EventQueue, sample_event):
    for i in range(3):
        evt = sample_event.model_copy(update={"delivery_id": f"del-{i}"})
        env = QueueEnvelope(event=evt)
        await queue.publish(env)

    counts = await queue.count_by_state()
    assert counts["pending"] == 3
    assert counts["acked"] == 0


@pytest.mark.asyncio
async def test_repo_partitioning(queue: EventQueue, sample_event):
    evt_a = sample_event.model_copy()
    evt_b = sample_event.model_copy(
        update={
            "repo": RepoRef(owner="other", name="repo", id=999),
            "delivery_id": "del-other-001",
        }
    )

    await queue.publish(QueueEnvelope(event=evt_a))
    await queue.publish(QueueEnvelope(event=evt_b))

    consumed_a = await queue.consume(repo_id="acme/widgets")
    consumed_b = await queue.consume(repo_id="other/repo")

    assert consumed_a is not None
    assert consumed_b is not None
    assert consumed_a.event.repo.owner == "acme"
    assert consumed_b.event.repo.owner == "other"


@pytest.mark.asyncio
async def test_get_events_returns_recent_messages(queue: EventQueue, sample_event):
    """get_events() is the monitoring read path and was untested."""
    await queue.publish(QueueEnvelope(event=sample_event))
    rows = await queue.get_events(limit=10)
    assert isinstance(rows, list)
    assert rows, "published envelope should appear in get_events()"
    assert "del-001" in rows[0]["envelope_json"]


@pytest.mark.asyncio
async def test_get_events_respects_limit(queue: EventQueue):
    """Distinct delivery ids, since message_id is UNIQUE and dedupes."""
    for i in range(3):
        evt = VerdityEvent(
            delivery_id=f"del-limit-{i}",
            trigger_type=TriggerType.PR_OPENED,
            repo=RepoRef(owner="acme", name="widgets", id=123),
        )
        await queue.publish(QueueEnvelope(event=evt))

    rows = await queue.get_events(limit=2)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_get_events_raises_when_not_connected():
    queue = EventQueue(db_path=":memory:")
    with pytest.raises(RuntimeError, match="not connected"):
        await queue.get_events()


@pytest.mark.asyncio
async def test_publish_raises_when_not_connected():
    queue = EventQueue(db_path=":memory:")
    # Do NOT call connect()
    evt = VerdityEvent(
        delivery_id="del-test",
        trigger_type=TriggerType.PR_OPENED,
        repo=RepoRef(owner="acme", name="widgets", id=123),
    )
    with pytest.raises(RuntimeError, match="not connected"):
        await queue.publish(QueueEnvelope(event=evt))


@pytest.mark.asyncio
async def test_consume_raises_when_not_connected():
    queue = EventQueue(db_path=":memory:")
    with pytest.raises(RuntimeError, match="not connected"):
        await queue.consume()


@pytest.mark.asyncio
async def test_acknowledge_raises_when_not_connected():
    queue = EventQueue(db_path=":memory:")
    with pytest.raises(RuntimeError, match="not connected"):
        await queue.acknowledge("msg-id")


@pytest.mark.asyncio
async def test_nack_raises_when_not_connected():
    queue = EventQueue(db_path=":memory:")
    with pytest.raises(RuntimeError, match="not connected"):
        await queue.nack("msg-id", error_msg="fail")


@pytest.mark.asyncio
async def test_count_by_state_raises_when_not_connected():
    queue = EventQueue(db_path=":memory:")
    with pytest.raises(RuntimeError, match="not connected"):
        await queue.count_by_state()


@pytest.mark.asyncio
async def test_defer_raises_when_not_connected():
    queue = EventQueue(db_path=":memory:")
    with pytest.raises(RuntimeError, match="not connected"):
        await queue.defer("msg-id", datetime.now(UTC))


@pytest.mark.asyncio
async def test_defer_requeues_without_incrementing_retry_count(queue: EventQueue, sample_event):
    """defer() should requeue a message without incrementing retry_count.

    This is the core fix for SG-11: when backing off, messages should not be
    dead-lettered due to retry exhaustion.
    """
    envelope = QueueEnvelope(event=sample_event)
    msg_id = await queue.publish(envelope)

    # Consume the message
    consumed = await queue.consume()
    assert consumed is not None

    # Defer it for 1 second
    not_before = datetime.now(UTC) + timedelta(seconds=1)
    await queue.defer(msg_id, not_before)

    # Check retry_count is still 0 (not incremented)
    row = await queue._conn.execute_one(
        "SELECT retry_count, state, not_before FROM queue_messages WHERE message_id = ?",
        (msg_id,),
    )
    assert row is not None
    assert row["retry_count"] == 0  # key assertion: no increment
    assert row["state"] == "pending"
    assert row["not_before"] is not None


@pytest.mark.asyncio
async def test_defer_with_none_not_before_pushes_to_earliest(queue: EventQueue, sample_event):
    """defer() with not_before=None should make message available immediately.

    This allows clearing a defer when backoff expires.
    """
    envelope = QueueEnvelope(event=sample_event)
    msg_id = await queue.publish(envelope)

    # First defer with a future time
    not_before = datetime.now(UTC) + timedelta(hours=1)
    await queue.defer(msg_id, not_before)

    # Now remove the defer by setting to None (or very near-future)
    await queue.defer(msg_id, None)

    # Message should be consumable
    consumed = await queue.consume()
    assert consumed is not None
    assert consumed.event.delivery_id == "del-001"


@pytest.mark.asyncio
async def test_consume_skips_deferred_messages(queue: EventQueue, sample_event):
    """consume() should not return messages that are still deferred.

    Messages with not_before > now should remain in pending state but not
    be returned by consume().
    """
    envelope = QueueEnvelope(event=sample_event)
    msg_id = await queue.publish(envelope)

    # Defer for 1 hour
    not_before = datetime.now(UTC) + timedelta(hours=1)
    await queue.defer(msg_id, not_before)

    # Consume should return None since the only message is deferred
    result = await queue.consume()
    assert result is None

    # But the message should still be in pending state
    counts = await queue.count_by_state()
    assert counts["pending"] == 1


@pytest.mark.asyncio
async def test_consume_returns_ready_messages_skipping_deferred(queue: EventQueue, sample_event):
    """consume() should skip deferred messages and return ready ones.

    When multiple messages exist, deferred ones should be skipped to allow
    ready messages to be processed first.
    """
    # Message A: will be deferred
    evt_a = sample_event.model_copy(update={"delivery_id": "del-a"})
    env_a = QueueEnvelope(event=evt_a)
    msg_a_id = await queue.publish(env_a)

    # Message B: ready to go
    evt_b = sample_event.model_copy(update={"delivery_id": "del-b"})
    env_b = QueueEnvelope(event=evt_b)
    msg_b_id = await queue.publish(env_b)

    # Defer message A
    await queue.defer(msg_a_id, datetime.now(UTC) + timedelta(hours=1))

    # Consume should return message B (the only ready message)
    consumed = await queue.consume()
    assert consumed is not None
    assert consumed.event.delivery_id == "del-b"

    # Message A should still be pending and deferred
    result = await queue.consume()
    assert result is None  # nothing ready yet

    # Message A becomes ready after its defer time
    await queue.defer(msg_a_id, None)

    consumed = await queue.consume()
    assert consumed is not None
    assert consumed.event.delivery_id == "del-a"
