from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from app.models.crm.enums import AgentPresenceStatus
from app.models.crm.presence import AgentPresence
from app.schemas.crm.conversation import ConversationCreate
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox.routing import _list_active_agents


def test_manual_assignment_rejects_offline_agent(db_session, crm_contact, crm_agent, crm_team, person):
    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    db_session.add(
        AgentPresence(
            agent_id=crm_agent.id,
            status=AgentPresenceStatus.offline,
            manual_override_status=AgentPresenceStatus.offline,
            last_seen_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        conversation_service.assign_conversation(
            db_session,
            conversation_id=str(conversation.id),
            agent_id=str(crm_agent.id),
            team_id=str(crm_team.id),
            assigned_by_id=str(person.id),
        )
    assert exc.value.status_code == 409


def test_manual_assignment_rejects_agent_without_presence(db_session, crm_contact, crm_agent, crm_team, person):
    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )

    with pytest.raises(HTTPException) as exc:
        conversation_service.assign_conversation(
            db_session,
            conversation_id=str(conversation.id),
            agent_id=str(crm_agent.id),
            team_id=str(crm_team.id),
            assigned_by_id=str(person.id),
        )
    assert exc.value.status_code == 409


def test_auto_assignment_drops_unavailable_agent_and_keeps_team(db_session, crm_contact, crm_agent, crm_team):
    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
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
    assert assignment.team_id == crm_team.id
    assert assignment.agent_id is None


def test_auto_routing_excludes_agents_without_presence(db_session, crm_agent_team):
    agents = _list_active_agents(db_session, str(crm_agent_team.team_id))
    assert agents == []


def test_auto_routing_includes_online_agents_with_fresh_presence(db_session, crm_agent_team, crm_agent):
    db_session.add(
        AgentPresence(
            agent_id=crm_agent.id,
            status=AgentPresenceStatus.online,
            manual_override_status=None,
            last_seen_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    agents = _list_active_agents(db_session, str(crm_agent_team.team_id))
    assert [agent.id for agent in agents] == [crm_agent.id]
