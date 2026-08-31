"""
Tests for the durable Event Queue.
"""

from __future__ import annotations

import pytest

from verdity.event_queue import EventQueue
from verdity.schemas import QueueEnvelope, TriggerType, VerdityEvent, RepoRef


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
