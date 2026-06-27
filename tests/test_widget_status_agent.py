"""Tests for the assigned-agent payload surfaced on the widget status endpoint.

See ``_assigned_agent_payload`` (app/api/crm/widget_public.py), consumed by the
chat widget's header to show the agent's name, picture, and live presence dot.
"""

from datetime import UTC, datetime

from app.api.crm.widget_public import _assigned_agent_payload
from app.models.crm.conversation import ConversationAssignment
from app.models.crm.enums import AgentPresenceStatus
from app.models.crm.presence import AgentPresence
from app.schemas.crm.conversation import ConversationCreate
from app.services.crm import conversation as conversation_service


def _conversation(db_session, crm_contact):
    return conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )


def test_assigned_agent_payload_reports_online_agent(db_session, crm_contact, crm_agent, crm_team, person):
    conversation = _conversation(db_session, crm_contact)
    person.avatar_url = "/static/avatars/me.png"
    db_session.add(
        ConversationAssignment(
            conversation_id=conversation.id,
            team_id=crm_team.id,
            agent_id=crm_agent.id,
            is_active=True,
        )
    )
    db_session.add(
        AgentPresence(
            agent_id=crm_agent.id,
            status=AgentPresenceStatus.online,
            manual_override_status=None,
            last_seen_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    payload = _assigned_agent_payload(db_session, conversation.id)

    assert payload is not None
    assert payload["name"] == "Test User"
    assert payload["avatar_url"] == "/static/avatars/me.png"
    assert payload["status"] == "online"


def test_assigned_agent_payload_none_when_unassigned(db_session, crm_contact):
    conversation = _conversation(db_session, crm_contact)
    assert _assigned_agent_payload(db_session, conversation.id) is None


def test_assigned_agent_payload_none_for_team_only(db_session, crm_contact, crm_team):
    conversation = _conversation(db_session, crm_contact)
    db_session.add(
        ConversationAssignment(
            conversation_id=conversation.id,
            team_id=crm_team.id,
            agent_id=None,
            is_active=True,
        )
    )
    db_session.commit()

    assert _assigned_agent_payload(db_session, conversation.id) is None


def test_assigned_agent_payload_defaults_offline_without_presence(db_session, crm_contact, crm_agent, crm_team):
    conversation = _conversation(db_session, crm_contact)
    db_session.add(
        ConversationAssignment(
            conversation_id=conversation.id,
            team_id=crm_team.id,
            agent_id=crm_agent.id,
            is_active=True,
        )
    )
    db_session.commit()

    payload = _assigned_agent_payload(db_session, conversation.id)

    assert payload is not None
    assert payload["status"] == "offline"
