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
        event = normalize_webhook(
            event_name="pull_request", action="opened", delivery_id=delivery_id, payload=payload
        )
        assert event.pull_request.draft is True


class TestPushEvent:
    def test_push_normalizes_to_push_trigger(self, delivery_id):
        payload = {
            "action": None,
            "after": "newheadsha123",
            "repository": {"id": 1, "name": "main-repo", "owner": {"login": "acme"}},
        }
        event = normalize_webhook(
            event_name="push",
            action=None,
            delivery_id=delivery_id,
            payload=payload,
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
            event_name="nonexistent_event",
            action=None,
            delivery_id=delivery_id,
            payload=payload,
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


class TestUnknownActionFallback:
    """Cover the 'unknown action for known event' branch (line 66-70)."""

    def test_known_event_with_unknown_action_uses_name_only(self, delivery_id):
        """pull_request event with unknown action falls back to PR_OPENED."""
        payload = {
            "pull_request": {
                "number": 1,
                "head": {"sha": "a"},
                "base": {"sha": "b"},
                "draft": False,
            },
            "repository": {"id": 1, "name": "r", "owner": {"login": "o"}},
        }
        event = normalize_webhook(
            event_name="pull_request",
            action="unknown_action_xyz",
            delivery_id=delivery_id,
            payload=payload,
        )
        # Falls back to PR_OPENED via name-only trigger
        assert event.trigger_type == TriggerType.PR_OPENED

    def test_unknown_event_no_action_falls_back(self, delivery_id):
        """Completely unknown event with action=None falls back to PR_OPENED."""
        payload = {
            "repository": {"id": 1, "name": "r", "owner": {"login": "o"}},
        }
        event = normalize_webhook(
            event_name="totally_unknown_event",
            action="some_action",
            delivery_id=delivery_id,
            payload=payload,
        )
        # Falls back to PR_OPENED via generic trigger
        assert event.trigger_type == TriggerType.PR_OPENED

    def test_event_with_name_only_trigger_uses_fallback(self, delivery_id):
        """When event_name has a None-action entry, use it as fallback for unknown action.

        Triggers lines 64-69 (the 'if name_only_trigger is not None' branch).
        Uses 'push' event_name which is in the map with action=None.
        """
        payload = {
            "after": "newsha",
            "repository": {"id": 1, "name": "r", "owner": {"login": "o"}},
        }
        event = normalize_webhook(
            event_name="push",
            action="some_bogus_action",
            delivery_id=delivery_id,
            payload=payload,
        )
        # Falls back to PUSH via name-only trigger
        assert event.trigger_type == TriggerType.PUSH
