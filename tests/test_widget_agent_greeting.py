"""Tests for the auto-greeting sent when an agent first picks up a widget chat.

See ``maybe_send_agent_greeting`` (app/services/crm/widget/service.py) and the
hook in ``assign_conversation`` (app/services/crm/conversations/service.py).
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.models.crm.chat_widget import ChatWidgetConfig, WidgetVisitorSession
from app.models.crm.conversation import Message
from app.models.crm.enums import (
    AgentPresenceStatus,
    ChannelType,
    MessageDirection,
    MessageStatus,
)
from app.models.crm.presence import AgentPresence
from app.models.crm.team import CrmAgent
from app.models.person import Person
from app.schemas.crm.conversation import ConversationCreate
from app.services.crm import conversation as conversation_service


@pytest.fixture(autouse=True)
def _disable_websocket_broadcasts(monkeypatch):
    monkeypatch.setattr("app.websocket.broadcaster.broadcast_new_message", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.websocket.broadcaster.broadcast_to_widget_visitor", lambda *args, **kwargs: None)


def _online(db_session, agent):
    db_session.add(
        AgentPresence(
            agent_id=agent.id,
            status=AgentPresenceStatus.online,
            manual_override_status=None,
            last_seen_at=datetime.now(UTC),
        )
    )
    db_session.commit()


def _widget_conversation(db_session, crm_contact, *, greeting_enabled=True):
    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    config = ChatWidgetConfig(name="Web", agent_greeting_enabled=greeting_enabled)
    db_session.add(config)
    db_session.commit()
    db_session.refresh(config)

    session = WidgetVisitorSession(
        widget_config_id=config.id,
        visitor_token=uuid.uuid4().hex,
        conversation_id=conversation.id,
    )
    db_session.add(session)
    db_session.commit()
    return conversation


def _outbound_messages(db_session, conversation):
    return (
        db_session.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .filter(Message.direction == MessageDirection.outbound)
        .all()
    )


def test_greeting_sent_when_agent_picks_up_widget_chat(
    db_session, crm_contact, crm_agent, crm_team, crm_agent_team, person
):
    conversation = _widget_conversation(db_session, crm_contact)
    _online(db_session, crm_agent)

    assignment = conversation_service.assign_conversation(
        db_session,
        conversation_id=str(conversation.id),
        agent_id=str(crm_agent.id),
        team_id=str(crm_team.id),
        assigned_by_id=str(person.id),
    )

    messages = _outbound_messages(db_session, conversation)
    assert len(messages) == 1
    assert messages[0].author_id == person.id
    assert messages[0].channel_type == ChannelType.chat_widget
    assert "assisting you today" in messages[0].body
    assert assignment is not None
    db_session.refresh(assignment)
    assert assignment.first_response_message_id == messages[0].id
    assert assignment.response_time_seconds is not None


def test_greeting_skipped_when_disabled_on_widget(db_session, crm_contact, crm_agent, crm_team, crm_agent_team, person):
    conversation = _widget_conversation(db_session, crm_contact, greeting_enabled=False)
    _online(db_session, crm_agent)

    conversation_service.assign_conversation(
        db_session,
        conversation_id=str(conversation.id),
        agent_id=str(crm_agent.id),
        team_id=str(crm_team.id),
        assigned_by_id=str(person.id),
    )

    assert _outbound_messages(db_session, conversation) == []


def test_greeting_fires_again_for_second_agent_on_handoff(
    db_session, crm_contact, crm_agent, crm_team, crm_agent_team, person
):
    conversation = _widget_conversation(db_session, crm_contact)
    _online(db_session, crm_agent)

    # First agent picks up and is introduced.
    conversation_service.assign_conversation(
        db_session,
        conversation_id=str(conversation.id),
        agent_id=str(crm_agent.id),
        team_id=str(crm_team.id),
        assigned_by_id=str(person.id),
    )
    assert len(_outbound_messages(db_session, conversation)) == 1

    # Hand off to a different agent — they introduce themselves too.
    second_person = Person(first_name="Bola", last_name="Ade", email="handoff-agent@example.com")
    db_session.add(second_person)
    db_session.commit()
    db_session.refresh(second_person)
    second_agent = CrmAgent(person_id=second_person.id, title="Backup Agent")
    db_session.add(second_agent)
    db_session.commit()
    db_session.refresh(second_agent)
    _online(db_session, second_agent)

    conversation_service.assign_conversation(
        db_session,
        conversation_id=str(conversation.id),
        agent_id=str(second_agent.id),
        team_id=str(crm_team.id),
        assigned_by_id=str(person.id),
    )

    messages = _outbound_messages(db_session, conversation)
    assert len(messages) == 2
    assert {m.author_id for m in messages} == {person.id, second_person.id}


def test_greeting_skipped_when_agent_already_messaged(
    db_session, crm_contact, crm_agent, crm_team, crm_agent_team, person
):
    conversation = _widget_conversation(db_session, crm_contact)
    _online(db_session, crm_agent)
    db_session.add(
        Message(
            conversation_id=conversation.id,
            channel_type=ChannelType.chat_widget,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body="Already here",
            author_id=person.id,
            sent_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    conversation_service.assign_conversation(
        db_session,
        conversation_id=str(conversation.id),
        agent_id=str(crm_agent.id),
        team_id=str(crm_team.id),
        assigned_by_id=str(person.id),
    )

    # Still only the single pre-existing outbound message — no greeting added.
    assert len(_outbound_messages(db_session, conversation)) == 1


def test_greeting_skipped_for_team_only_assignment(db_session, crm_contact, crm_agent, crm_team, crm_agent_team):
    conversation = _widget_conversation(db_session, crm_contact)
    # Offline agent + auto-routing => agent is dropped, assignment is team-only.
    db_session.add(
        AgentPresence(
            agent_id=crm_agent.id,
            status=AgentPresenceStatus.offline,
            manual_override_status=AgentPresenceStatus.offline,
            last_seen_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    assignment = conversation_service.assign_conversation(
        db_session,
        conversation_id=str(conversation.id),
        agent_id=str(crm_agent.id),
        team_id=str(crm_team.id),
        assigned_by_id=None,
    )

    assert assignment is not None
    assert assignment.agent_id is None
    assert _outbound_messages(db_session, conversation) == []


def test_greeting_skipped_for_non_widget_conversation(
    db_session, crm_contact, crm_agent, crm_team, crm_agent_team, person
):
    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    _online(db_session, crm_agent)

    conversation_service.assign_conversation(
        db_session,
        conversation_id=str(conversation.id),
        agent_id=str(crm_agent.id),
        team_id=str(crm_team.id),
        assigned_by_id=str(person.id),
    )

    assert _outbound_messages(db_session, conversation) == []
