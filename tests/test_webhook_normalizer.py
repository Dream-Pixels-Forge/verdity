"""
Tests for webhook payload normalization.
"""

from __future__ import annotations

import pytest

from verdity.schemas import TriggerType
from verdity.webhook_normalizer import normalize_webhook


@pytest.fixture
def delivery_id():
    return "d12e3456-7890-abcd-ef00-112233445566"


class TestPrOpened:
    def test_normalizes_to_pr_opened(self, delivery_id):
        payload = {
            "action": "opened",
            "pull_request": {
                "number": 42,
                "head": {"sha": "abc123"},
                "base": {"sha": "def456"},
                "draft": False,
            },
            "repository": {
                "id": 100,
                "name": "test-repo",
                "owner": {"login": "test-org"},
            },
        }
        event = normalize_webhook(
            event_name="pull_request",
            action="opened",
            delivery_id=delivery_id,
            payload=payload,
        )
        assert event.trigger_type == TriggerType.PR_OPENED
        assert event.repo.owner == "test-org"
        assert event.repo.name == "test-repo"
        assert event.repo.id == 100
        assert event.pull_request.number == 42
        assert event.pull_request.head_sha == "abc123"
        assert event.pull_request.base_sha == "def456"
        assert event.pull_request.draft is False
        assert event.delivery_id == delivery_id

    def test_draft_pr(self, delivery_id):
        payload = {
            "action": "opened",
            "pull_request": {
                "number": 10,
                "head": {"sha": "sha1"},
                "base": {"sha": "sha2"},
                "draft": True,
            },
            "repository": {"id": 1, "name": "r", "owner": {"login": "o"}},
        }
        event = normalize_webhook(event_name="pull_request", action="opened",
                                  delivery_id=delivery_id, payload=payload)
        assert event.pull_request.draft is True


class TestPushEvent:
    def test_push_normalizes_to_push_trigger(self, delivery_id):
        payload = {
            "action": None,
            "after": "newheadsha123",
            "repository": {"id": 1, "name": "main-repo", "owner": {"login": "acme"}},
        }
        event = normalize_webhook(
            event_name="push", action=None,
            delivery_id=delivery_id, payload=payload,
        )
        assert event.trigger_type == TriggerType.PUSH
        assert event.push_ref == "newheadsha123"
        assert event.pull_request is None


class TestUnknownEvent:
    def test_unknown_event_still_normalizes(self, delivery_id):
        payload = {
            "repository": {"id": 1, "name": "r", "owner": {"login": "o"}},
        }
        event = normalize_webhook(
            event_name="nonexistent_event", action=None,
            delivery_id=delivery_id, payload=payload,
        )
        # Should not raise; trigger_type should reflect the event name
        assert event.trigger_type is not None
        assert event.repo.owner == "o"


class TestDeliveryIdValidation:
    def test_empty_delivery_id_raises(self):
        import pytest
        from verdity.schemas import VerdityEvent
        with pytest.raises(Exception):  # Pydantic validation error
            VerdityEvent(
                delivery_id="",
                trigger_type=TriggerType.PR_OPENED,
                repo={"owner": "o", "name": "r", "id": 1},
            )
