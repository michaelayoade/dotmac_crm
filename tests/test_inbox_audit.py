"""Tests for inbox audit helpers."""

from unittest.mock import patch

from app.services.crm.inbox.audit import log_conversation_action


def test_log_conversation_action_calls_audit():
    with patch("app.services.crm.inbox.audit.log_audit_event") as mock_log:
        log_conversation_action(
            None,
            action="assign_conversation",
            conversation_id="conv-1",
            actor_id="person-1",
            metadata={"agent_id": "agent-1"},
        )
        mock_log.assert_called_once()
