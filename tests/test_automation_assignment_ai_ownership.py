import uuid
from datetime import UTC, datetime

from app.models.crm.ai_intake import AiIntakeConfig
from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.services.automation_actions import execute_actions
from app.services.events.types import Event, EventType


def test_assignment_automations_skip_conversation_while_ai_owns_it(db_session, crm_contact):
    conversation = Conversation(
        person_id=crm_contact.id,
        status=ConversationStatus.pending,
        metadata_={"ai_intake": {"status": "awaiting_customer"}},
    )
    db_session.add(conversation)
    db_session.commit()

    event = Event(
        event_type=EventType.message_inbound,
        payload={"conversation_id": str(conversation.id)},
    )
    results = execute_actions(
        db_session,
        [
            {"action_type": "assign_conversation", "params": {}},
            {"action_type": "assign_conversation_auto", "params": {}},
        ],
        event,
        triggered_by_automation=True,
    )

    assert [result["success"] for result in results] == [True, True]
    assert (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .count()
        == 0
    )


def test_assignment_automation_respects_ai_preclaim_before_state_is_persisted(db_session, crm_contact, monkeypatch):
    widget_id = uuid.uuid4()
    conversation = Conversation(person_id=crm_contact.id, status=ConversationStatus.open)
    db_session.add(conversation)
    db_session.flush()
    message = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.chat_widget,
        direction=MessageDirection.inbound,
        status=MessageStatus.received,
        body="I need help",
        received_at=datetime.now(UTC),
        metadata_={"widget_config_id": str(widget_id)},
    )
    config = AiIntakeConfig(
        scope_key=f"widget:{widget_id}",
        channel_type=ChannelType.chat_widget,
        is_enabled=True,
        department_mappings=[{"key": "support", "label": "Support", "team_id": None}],
    )
    db_session.add_all([message, config])
    db_session.commit()
    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")

    results = execute_actions(
        db_session,
        [{"action_type": "assign_conversation_auto", "params": {}}],
        Event(
            event_type=EventType.message_inbound,
            payload={
                "conversation_id": str(conversation.id),
                "message_id": str(message.id),
            },
        ),
        triggered_by_automation=True,
    )

    assert results == [{"action_type": "assign_conversation_auto", "success": True, "error": None}]
    assert conversation.metadata_ is None
    assert (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .count()
        == 0
    )
