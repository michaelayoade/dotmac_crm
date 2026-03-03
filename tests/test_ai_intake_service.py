import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.crm.ai_intake import AiIntakeConfig
from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.models.crm.team import CrmTeam
from app.models.person import Person
from app.services.crm.ai_intake import (
    AI_INTAKE_METADATA_KEY,
    escalate_expired_pending_intakes,
    make_scope_key,
    process_pending_intake,
    save_ai_intake_config,
)


def _make_person(db_session):
    person = Person(email=f"ai-intake-{uuid.uuid4().hex[:8]}@example.com", first_name="AI", last_name="Test")
    db_session.add(person)
    db_session.flush()
    return person


def _make_conversation(db_session, person):
    conversation = Conversation(person_id=person.id, status=ConversationStatus.open, is_active=True)
    db_session.add(conversation)
    db_session.flush()
    return conversation


def _make_message(db_session, conversation, *, body="Need help", metadata=None):
    message = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.chat_widget,
        direction=MessageDirection.inbound,
        status=MessageStatus.received,
        body=body,
        metadata_=metadata or {"widget_config_id": str(uuid.uuid4())},
    )
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)
    db_session.refresh(conversation)
    return message


def _make_config(db_session, *, scope_key, team_id=None, exclude_campaign_attribution=True):
    config = AiIntakeConfig(
        scope_key=scope_key,
        channel_type=ChannelType.chat_widget,
        is_enabled=True,
        confidence_threshold=0.75,
        allow_followup_questions=True,
        max_clarification_turns=1,
        escalate_after_minutes=5,
        exclude_campaign_attribution=exclude_campaign_attribution,
        fallback_team_id=team_id,
        department_mappings=[
            {
                "key": "support",
                "label": "Support",
                "team_id": str(team_id) if team_id else str(uuid.uuid4()),
                "tags": ["support"],
                "priority": "high",
                "notify_email": "",
            }
        ],
    )
    db_session.add(config)
    db_session.commit()
    return config


def test_make_scope_key_for_widget():
    widget_id = str(uuid.uuid4())
    assert make_scope_key(channel_type=ChannelType.chat_widget, widget_config_id=widget_id) == f"widget:{widget_id}"


def test_process_pending_intake_excludes_campaign_attribution(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    message = _make_message(
        db_session,
        conversation,
        metadata={"widget_config_id": widget_id, "attribution": {"utm_source": "meta", "campaign_id": "c1"}},
    )
    _make_config(db_session, scope_key=f"widget:{widget_id}")

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)

    result = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )

    db_session.refresh(conversation)
    assert result.handled is False
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["status"] == "excluded"


def test_process_pending_intake_resolves_and_assigns_team(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    message = _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})
    team = CrmTeam(name="Support", is_active=True)
    db_session.add(team)
    db_session.commit()
    _make_config(db_session, scope_key=f"widget:{widget_id}", team_id=team.id)

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)
    monkeypatch.setattr(
        "app.services.crm.ai_intake.ai_gateway.generate_with_fallback",
        lambda db, **kwargs: (
            SimpleNamespace(
                content='{"department":"support","confidence":0.93,"reason":"service issue","needs_followup":false,"followup_question":""}'
            ),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )

    result = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )

    db_session.refresh(conversation)
    assignment = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )
    assert result.handled is True
    assert result.resolved is True
    assert conversation.status == ConversationStatus.open
    assert assignment is not None
    assert assignment.team_id == team.id


def test_process_pending_intake_sends_followup_when_uncertain(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    message = _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})
    _make_config(db_session, scope_key=f"widget:{widget_id}")

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)
    monkeypatch.setattr(
        "app.services.crm.ai_intake.ai_gateway.generate_with_fallback",
        lambda db, **kwargs: (
            SimpleNamespace(
                content='{"department":null,"confidence":0.41,"reason":"needs more context","needs_followup":true,"followup_question":"Is this about an existing service issue or a new order?"}'
            ),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )
    sent = {}

    def _fake_send_message(db, payload, author_id=None, trace_id=None):
        sent["body"] = payload.body
        outbound = Message(
            conversation_id=payload.conversation_id,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=payload.body,
        )
        db.add(outbound)
        db.commit()
        db.refresh(outbound)
        return outbound

    monkeypatch.setattr("app.services.crm.ai_intake.send_message", _fake_send_message)

    result = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )

    db_session.refresh(conversation)
    assert result.handled is True
    assert result.followup_sent is True
    assert conversation.status == ConversationStatus.pending
    assert sent["body"] == "Is this about an existing service issue or a new order?"
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["turn_count"] == 1


def test_process_pending_intake_waits_for_timeout_when_followup_disabled(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    widget_id = str(uuid.uuid4())
    message = _make_message(db_session, conversation, metadata={"widget_config_id": widget_id})
    team = CrmTeam(name="Live", is_active=True)
    db_session.add(team)
    db_session.commit()
    config = _make_config(db_session, scope_key=f"widget:{widget_id}", team_id=team.id)
    config.allow_followup_questions = False
    config.max_clarification_turns = 0
    config.escalate_after_minutes = 15
    db_session.commit()

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")
    monkeypatch.setattr("app.services.crm.ai_intake.ai_gateway.enabled", lambda db: True)
    monkeypatch.setattr(
        "app.services.crm.ai_intake.ai_gateway.generate_with_fallback",
        lambda db, **kwargs: (
            SimpleNamespace(
                content='{"department":null,"confidence":0.22,"reason":"unclear","needs_followup":true,"followup_question":"What do you need help with?"}'
            ),
            {"endpoint": "primary", "fallback_used": False},
        ),
    )

    result = process_pending_intake(
        db_session,
        conversation=conversation,
        message=message,
        scope_key=f"widget:{widget_id}",
        is_new_conversation=True,
    )

    db_session.refresh(conversation)
    state = conversation.metadata_[AI_INTAKE_METADATA_KEY]
    assert result.handled is True
    assert result.followup_sent is False
    assert result.escalated is False
    assert conversation.status == ConversationStatus.pending
    assert state["status"] == "awaiting_timeout"


def test_escalate_expired_pending_intakes_opens_and_assigns_fallback_team(db_session, monkeypatch):
    person = _make_person(db_session)
    conversation = _make_conversation(db_session, person)
    team = CrmTeam(name="Fallback", is_active=True)
    db_session.add(team)
    db_session.commit()
    widget_id = str(uuid.uuid4())
    _make_config(db_session, scope_key=f"widget:{widget_id}", team_id=team.id)
    conversation.status = ConversationStatus.pending
    conversation.metadata_ = {
        AI_INTAKE_METADATA_KEY: {
            "status": "awaiting_timeout",
            "scope_key": f"widget:{widget_id}",
            "started_at": (datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
            "escalate_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "turn_count": 1,
        }
    }
    db_session.commit()

    monkeypatch.setenv("CRM_AI_PENDING_INTAKE_ENABLED", "1")

    result = escalate_expired_pending_intakes(db_session, limit=20)

    db_session.refresh(conversation)
    assignment = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )
    assert result["escalated"] == 1
    assert conversation.status == ConversationStatus.open
    assert conversation.metadata_[AI_INTAKE_METADATA_KEY]["status"] == "escalated"
    assert assignment is not None
    assert assignment.team_id == team.id


def test_save_ai_intake_config_requires_fallback_team_when_enabled(db_session):
    with pytest.raises(ValueError, match="fallback live team is required"):
        save_ai_intake_config(
            db_session,
            scope_key="widget:test",
            channel_type=ChannelType.chat_widget,
            enabled=True,
            confidence_threshold="0.75",
            allow_followup_questions=True,
            max_clarification_turns="1",
            escalate_after_minutes="5",
            exclude_campaign_attribution=True,
            fallback_team_id="",
            instructions="Ask enough to identify intent.",
            department_mappings_json='[{"key":"support","label":"Support","team_id":null}]',
        )
