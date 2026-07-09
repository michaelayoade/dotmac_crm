from datetime import UTC, datetime
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.models.crm.enums import ChannelType, ConversationStatus
from app.services.crm.inbox.conversation_status import (
    _build_resolved_closing_message,
    _should_send_resolved_closing_message,
    update_conversation_status,
)


class _FakeConversation:
    def __init__(self, status: ConversationStatus):
        self.status = status
        self.metadata_ = {}
        self.created_at = datetime.now(UTC)
        self.resolved_at = None
        self.resolution_time_seconds = None
        self.ticket_id = None


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
            conversation=fake_conversation,
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


def test_update_conversation_status_ticket_handoff_stores_metadata_and_audit_context():
    conversation_id = str(uuid4())
    ticket_id = str(uuid4())
    fake_conversation = _FakeConversation(ConversationStatus.open)
    fake_conversation.ticket_id = ticket_id
    db = Mock()
    db.get.return_value = fake_conversation

    with (
        patch("app.services.crm.inbox.conversation_status.conversation_service") as mock_service,
        patch("app.services.crm.inbox.conversation_status.log_conversation_action") as mock_log,
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
        from app.services.crm.inbox.conversation_status import ResolutionContext

        mock_service.Conversations.get.return_value = _FakeConversation(ConversationStatus.open)
        mock_resolve_channel.return_value = ChannelType.whatsapp
        mock_select_variant.return_value = "social"
        mock_claim_send.return_value = True
        mock_send_resolved_closing.return_value = (True, "msg-1", "whatsapp", None)
        result = update_conversation_status(
            db,
            conversation_id=conversation_id,
            new_status="resolved_to_ticket",
            actor_id="person-1",
            resolution_context=ResolutionContext(
                mode="ticket_handoff",
                label="Sent to ticket",
                ticket_id=ticket_id,
                ticket_reference="TCK-1001",
            ),
        )

        assert result.kind == "updated"
        assert fake_conversation.metadata_["resolution"]["mode"] == "ticket_handoff"
        assert fake_conversation.metadata_["resolution"]["ticket_reference"] == "TCK-1001"
        mock_log.assert_called_once_with(
            db,
            action="update_status",
            conversation_id=conversation_id,
            actor_id="person-1",
            metadata={
                "status": "resolved_to_ticket",
                "resolution_mode": "ticket_handoff",
                "resolution_label": "Sent to ticket",
                "ticket_id": ticket_id,
                "ticket_reference": "TCK-1001",
            },
        )
        mock_send_resolved_closing.assert_called_once_with(
            db,
            conversation_id=conversation_id,
            conversation=fake_conversation,
            channel_type=ChannelType.whatsapp,
            variant="social",
            actor_id="person-1",
        )
        mock_persist_resolved_closing.assert_called_once()


def test_build_ticket_handoff_message_includes_ticket_reference():
    fake_conversation = _FakeConversation(ConversationStatus.resolved_to_ticket)
    fake_conversation.metadata_ = {
        "resolution": {
            "mode": "ticket_handoff",
            "ticket_reference": "TCK-1001",
        }
    }

    subject, body = _build_resolved_closing_message(
        Mock(),
        conversation=fake_conversation,
        channel_type=ChannelType.email,
        variant="social",
    )

    assert subject == "Support Ticket Created: TCK-1001"
    assert "Your issue is currently being worked on by our support team." in body
    assert "Your ticket ID is: TCK-1001" in body
    assert "please reference this ticket ID" in body
    assert "How was your experience" not in body


def test_feedback_variant_uses_configured_outro_for_email():
    fake_conversation = _FakeConversation(ConversationStatus.resolved)
    db = Mock()

    with patch(
        "app.services.crm.inbox.conversation_status.resolve_value",
        return_value="Thank you for chatting with Dotmac today.",
    ):
        subject, body = _build_resolved_closing_message(
            db,
            conversation=fake_conversation,
            channel_type=ChannelType.email,
            variant="feedback",
        )

    assert subject == "Support Request Resolved"
    assert body == "Thank you for chatting with Dotmac today."
    assert "We would appreciate your feedback" not in body


def test_feedback_variant_uses_configured_outro_for_whatsapp():
    fake_conversation = _FakeConversation(ConversationStatus.resolved)
    db = Mock()

    with patch(
        "app.services.crm.inbox.conversation_status.resolve_value",
        return_value="Thank you for chatting with Dotmac today.",
    ):
        subject, body = _build_resolved_closing_message(
            db,
            conversation=fake_conversation,
            channel_type=ChannelType.whatsapp,
            variant="feedback",
        )

    assert subject is None
    assert body == "Thank you for chatting with Dotmac today."
    assert "We'd really appreciate your feedback" not in body


def test_ticket_handoff_does_not_resend_for_same_ticket():
    fake_conversation = _FakeConversation(ConversationStatus.open)
    fake_conversation.metadata_ = {
        "resolution": {
            "mode": "ticket_handoff",
            "ticket_id": "ticket-1",
            "ticket_reference": "TCK-1001",
        },
        "resolved_closing_message": {
            "ticket_handoffs": {
                "ticket:ticket-1": {
                    "sent_at": datetime.now(UTC).isoformat(),
                    "message_id": "msg-1",
                }
            }
        },
    }

    assert not _should_send_resolved_closing_message(
        conversation=fake_conversation,
        status_enum=ConversationStatus.resolved_to_ticket,
        previous_status=ConversationStatus.open,
    )


def test_ticket_handoff_resends_for_new_ticket_after_reopen():
    fake_conversation = _FakeConversation(ConversationStatus.open)
    fake_conversation.metadata_ = {
        "resolution": {
            "mode": "ticket_handoff",
            "ticket_id": "ticket-2",
            "ticket_reference": "TCK-1002",
        },
        "resolved_closing_message": {
            "ticket_handoffs": {
                "ticket:ticket-1": {
                    "sent_at": datetime.now(UTC).isoformat(),
                    "message_id": "msg-1",
                }
            }
        },
    }

    assert _should_send_resolved_closing_message(
        conversation=fake_conversation,
        status_enum=ConversationStatus.resolved_to_ticket,
        previous_status=ConversationStatus.open,
    )


def test_ticket_handoff_sends_when_status_already_resolved_to_ticket_without_ticket_marker():
    fake_conversation = _FakeConversation(ConversationStatus.resolved_to_ticket)
    fake_conversation.metadata_ = {
        "resolution": {
            "mode": "ticket_handoff",
            "ticket_id": "ticket-1",
            "ticket_reference": "TCK-1001",
        },
        "resolved_closing_message": {
            "sent_at": datetime.now(UTC).isoformat(),
            "message_id": "legacy-msg",
        },
    }

    assert _should_send_resolved_closing_message(
        conversation=fake_conversation,
        status_enum=ConversationStatus.resolved_to_ticket,
        previous_status=ConversationStatus.resolved_to_ticket,
    )


def test_update_conversation_status_normal_resolve_clears_handoff_metadata():
    conversation_id = str(uuid4())
    fake_conversation = _FakeConversation(ConversationStatus.open)
    fake_conversation.metadata_ = {
        "resolution": {
            "mode": "ticket_handoff",
            "ticket_reference": "TCK-1001",
        }
    }
    db = Mock()
    db.get.return_value = fake_conversation

    with (
        patch("app.services.crm.inbox.conversation_status.conversation_service") as mock_service,
        patch("app.services.crm.inbox.conversation_status.log_conversation_action") as mock_log,
        patch("app.services.crm.inbox.conversation_status.queue_for_resolved_conversation"),
        patch("app.services.crm.inbox.conversation_status._resolve_latest_channel_type") as mock_resolve_channel,
        patch("app.services.crm.inbox.conversation_status._claim_resolved_closing_message_send") as mock_claim_send,
        patch("app.services.crm.inbox.conversation_status._send_resolved_closing_message"),
        patch("app.services.crm.inbox.conversation_status._persist_resolved_closing_message_metadata"),
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
        assert "resolution" not in fake_conversation.metadata_
        mock_claim_send.assert_not_called()
        mock_log.assert_called_once_with(
            db,
            action="update_status",
            conversation_id=conversation_id,
            actor_id="person-1",
            metadata={"status": "resolved"},
        )
