from unittest.mock import patch

from fastapi import HTTPException

from app.models.crm.enums import ConversationStatus
from app.services.crm.inbox.conversation_status import update_conversation_status


class _FakeConversation:
    def __init__(self, status: ConversationStatus):
        self.status = status


def test_update_conversation_status_invalid_transition():
    with patch(
        "app.services.crm.inbox.conversation_status.conversation_service"
    ) as mock_service:
        mock_service.Conversations.get.return_value = _FakeConversation(
            ConversationStatus.resolved
        )
        result = update_conversation_status(
            None,
            conversation_id="conv-1",
            new_status="pending",
            actor_id="person-1",
        )
        assert result.kind == "invalid_transition"
        mock_service.Conversations.update.assert_not_called()


def test_update_conversation_status_valid_transition():
    with patch(
        "app.services.crm.inbox.conversation_status.conversation_service"
    ) as mock_service, patch(
        "app.services.crm.inbox.conversation_status.log_conversation_action"
    ) as mock_log:
        mock_service.Conversations.get.return_value = _FakeConversation(
            ConversationStatus.open
        )
        result = update_conversation_status(
            None,
            conversation_id="conv-1",
            new_status="resolved",
            actor_id="person-1",
        )
        assert result.kind == "updated"
        mock_service.Conversations.update.assert_called_once()
        mock_log.assert_called_once()


def test_update_conversation_status_not_found():
    with patch(
        "app.services.crm.inbox.conversation_status.conversation_service"
    ) as mock_service:
        mock_service.Conversations.get.side_effect = HTTPException(
            status_code=404, detail="Conversation not found"
        )
        result = update_conversation_status(
            None,
            conversation_id="conv-1",
            new_status="resolved",
            actor_id="person-1",
        )
        assert result.kind == "not_found"
