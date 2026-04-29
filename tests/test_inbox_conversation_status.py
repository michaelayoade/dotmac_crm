from datetime import UTC, datetime
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.models.crm.enums import ChannelType, ConversationStatus
from app.services.crm.inbox.conversation_status import update_conversation_status


class _FakeConversation:
    def __init__(self, status: ConversationStatus):
        self.status = status
        self.metadata_ = {}
        self.created_at = datetime.now(UTC)
        self.resolved_at = None
        self.resolution_time_seconds = None


def test_update_conversation_status_invalid_transition():
    with patch("app.services.crm.inbox.conversation_status.conversation_service") as mock_service:
        mock_service.Conversations.get.return_value = _FakeConversation(ConversationStatus.resolved)
        result = update_conversation_status(
            None,
            conversation_id="conv-1",
            new_status="pending",
            actor_id="person-1",
        )
        assert result.kind == "invalid_transition"
        mock_service.Conversations.update.assert_not_called()


def test_update_conversation_status_valid_transition():
    with (
        patch("app.services.crm.inbox.conversation_status.conversation_service") as mock_service,
        patch("app.services.crm.inbox.conversation_status.log_conversation_action") as mock_log,
    ):
        mock_service.Conversations.get.return_value = _FakeConversation(ConversationStatus.open)
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
    with patch("app.services.crm.inbox.conversation_status.conversation_service") as mock_service:
        mock_service.Conversations.get.side_effect = HTTPException(status_code=404, detail="Conversation not found")
        result = update_conversation_status(
            None,
            conversation_id="conv-1",
            new_status="resolved",
            actor_id="person-1",
        )
        assert result.kind == "not_found"


def test_update_conversation_status_resolved_sends_unified_closing_message_once():
    conversation_id = str(uuid4())
    fake_conversation = _FakeConversation(ConversationStatus.open)
    db = Mock()
    db.get.return_value = fake_conversation

    with (
        patch("app.services.crm.inbox.conversation_status.conversation_service") as mock_service,
        patch("app.services.crm.inbox.conversation_status.log_conversation_action"),
        patch("app.services.crm.inbox.conversation_status.queue_for_resolved_conversation"),
        patch("app.services.crm.inbox.conversation_status._resolve_latest_channel_type") as mock_resolve_channel,
        patch("app.services.crm.inbox.conversation_status._select_resolved_closing_variant") as mock_select_variant,
        patch("app.services.crm.inbox.conversation_status._claim_resolved_closing_message_send") as mock_claim_send,
        patch(
            "app.services.crm.inbox.conversation_status._send_resolved_closing_message"
        ) as mock_send_resolved_closing,
        patch(
            "app.services.crm.inbox.conversation_status._persist_resolved_closing_message_metadata"
        ) as mock_persist_resolved_closing,
        patch("app.services.crm.inbox.summaries.recompute_conversation_summary"),
    ):
        mock_service.Conversations.get.return_value = _FakeConversation(ConversationStatus.open)
        mock_resolve_channel.return_value = ChannelType.whatsapp
        mock_select_variant.return_value = "social"
        mock_claim_send.return_value = True
        mock_send_resolved_closing.return_value = (True, "msg-1", "whatsapp", None)

        result = update_conversation_status(
            db,
            conversation_id=conversation_id,
            new_status="resolved",
            actor_id="person-1",
        )

        assert result.kind == "updated"
        mock_send_resolved_closing.assert_called_once_with(
            db,
            conversation_id=conversation_id,
            channel_type=ChannelType.whatsapp,
            variant="social",
            actor_id="person-1",
        )
        mock_persist_resolved_closing.assert_called_once()


def test_update_conversation_status_resolved_skips_unified_closing_when_already_sent():
    conversation_id = str(uuid4())
    fake_conversation = _FakeConversation(ConversationStatus.open)
    fake_conversation.metadata_ = {
        "resolved_closing_message": {"sent_at": datetime.now(UTC).isoformat()},
    }
    db = Mock()
    db.get.return_value = fake_conversation

    with (
        patch("app.services.crm.inbox.conversation_status.conversation_service") as mock_service,
        patch("app.services.crm.inbox.conversation_status.log_conversation_action"),
        patch("app.services.crm.inbox.conversation_status.queue_for_resolved_conversation"),
        patch("app.services.crm.inbox.conversation_status._resolve_latest_channel_type") as mock_resolve_channel,
        patch("app.services.crm.inbox.conversation_status._claim_resolved_closing_message_send") as mock_claim_send,
        patch(
            "app.services.crm.inbox.conversation_status._send_resolved_closing_message"
        ) as mock_send_resolved_closing,
        patch(
            "app.services.crm.inbox.conversation_status._persist_resolved_closing_message_metadata"
        ) as mock_persist_resolved_closing,
        patch("app.services.crm.inbox.summaries.recompute_conversation_summary"),
    ):
        mock_service.Conversations.get.return_value = _FakeConversation(ConversationStatus.open)
        mock_resolve_channel.return_value = ChannelType.whatsapp

        result = update_conversation_status(
            db,
            conversation_id=conversation_id,
            new_status="resolved",
            actor_id="person-1",
        )

        assert result.kind == "updated"
        mock_claim_send.assert_not_called()
        mock_send_resolved_closing.assert_not_called()
        mock_persist_resolved_closing.assert_not_called()


def test_update_conversation_status_resolved_queues_csat_only_for_chat_widget():
    conversation_id = str(uuid4())
    fake_conversation = _FakeConversation(ConversationStatus.open)
    db = Mock()
    db.get.return_value = fake_conversation

    with (
        patch("app.services.crm.inbox.conversation_status.conversation_service") as mock_service,
        patch("app.services.crm.inbox.conversation_status.log_conversation_action"),
        patch("app.services.crm.inbox.conversation_status.queue_for_resolved_conversation") as mock_queue_csat,
        patch("app.services.crm.inbox.conversation_status._resolve_latest_channel_type") as mock_resolve_channel,
        patch(
            "app.services.crm.inbox.conversation_status._send_resolved_closing_message"
        ) as mock_send_resolved_closing,
        patch("app.services.crm.inbox.summaries.recompute_conversation_summary"),
    ):
        mock_service.Conversations.get.return_value = _FakeConversation(ConversationStatus.open)
        mock_resolve_channel.return_value = ChannelType.chat_widget

        result = update_conversation_status(
            db,
            conversation_id=conversation_id,
            new_status="resolved",
            actor_id="person-1",
        )

        assert result.kind == "updated"
        mock_queue_csat.assert_called_once_with(
            db,
            conversation_id=conversation_id,
            author_id="person-1",
        )
        mock_send_resolved_closing.assert_not_called()
