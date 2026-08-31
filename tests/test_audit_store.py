"""
Tests for the Audit Store (non-negotiable constraint #9).
"""

from __future__ import annotations

import uuid

import pytest

from verdity.audit_store import AuditStore


@pytest.mark.asyncio
async def test_append_and_query_by_run(audit_store: AuditStore):
    run_id = uuid.uuid4()
    aid = await audit_store.append(
        event_type="finding.created",
        entity_type="finding",
        entity_id="find-001",
        payload={"summary": "SQL injection found", "confidence": 0.92},
        related_run_id=run_id,
    )
    assert aid > 0

    records = await audit_store.query_by_run(run_id)
    assert len(records) == 1
    assert records[0]["event_type"] == "finding.created"
    assert records[0]["entity_id"] == "find-001"


@pytest.mark.asyncio
async def test_append_and_query_by_entity(audit_store: AuditStore):
    await audit_store.append(
        event_type="routing.decision",
        entity_type="approval_queue",
        entity_id="aq-42",
        payload={"action": "approve"},
    )
    records = await audit_store.query_by_entity("approval_queue", "aq-42")
    assert len(records) == 1
    assert records[0]["payload"] == '{"action": "approve"}'


@pytest.mark.asyncio
async def test_append_multiple_events_same_run(audit_store: AuditStore):
    run_id = uuid.uuid4()
    for i in range(5):
        await audit_store.append(
            event_type="model.call",
            entity_type="token_meter",
            entity_id=f"call-{i}",
            payload={"tokens": 1000},
            related_run_id=run_id,
        )
    records = await audit_store.query_by_run(run_id)
    assert len(records) == 5


@pytest.mark.asyncio
async def test_payload_is_persisted_as_json(audit_store: AuditStore):
    import json

    payload = {"nested": {"value": 42}, "list": [1, 2, 3]}
    await audit_store.append(
        event_type="test.event",
        entity_type="test",
        entity_id="test-1",
        payload=payload,
    )
    records = await audit_store.query_by_entity("test", "test-1")
    stored = json.loads(records[0]["payload"])
    assert stored == payload


@pytest.mark.asyncio
async def test_append_raises_when_not_connected():
    store = AuditStore(db_path=":memory:")
    # Do NOT call connect()
    with pytest.raises(RuntimeError, match="not connected"):
        await store.append(
            event_type="finding.created",
            entity_type="finding",
            entity_id="find-001",
            payload={"summary": "test"},
        )


@pytest.mark.asyncio
async def test_query_by_run_raises_when_not_connected():
    store = AuditStore(db_path=":memory:")
    with pytest.raises(RuntimeError, match="not connected"):
        await store.query_by_run(uuid.uuid4())


@pytest.mark.asyncio
async def test_query_by_entity_raises_when_not_connected():
    store = AuditStore(db_path=":memory:")
    with pytest.raises(RuntimeError, match="not connected"):
        await store.query_by_entity("finding", "find-001")
