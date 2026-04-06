import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.models.crm.conversation import ConversationAssignment, Message
from app.models.crm.enums import AgentPresenceStatus, ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.models.crm.presence import AgentPresence
from app.models.crm.team import CrmAgent, CrmRoutingRule
from app.models.person import Person
from app.schemas.crm.conversation import ConversationCreate
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox.conversation_actions import assign_conversation as inbox_assign_conversation
from app.services.crm.inbox.routing import _list_active_agents, apply_routing_rules
from app.services.crm.presence import agent_presence as agent_presence_service


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


def test_inbox_assign_action_maps_conflict_to_invalid_input(db_session, crm_contact, crm_agent, crm_team, person):
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

    result = inbox_assign_conversation(
        db_session,
        conversation_id=str(conversation.id),
        agent_id=str(crm_agent.id),
        team_id=str(crm_team.id),
        assigned_by_id=str(person.id),
    )

    assert result.kind == "invalid_input"
    assert "offline or unavailable" in (result.error_detail or "")


def test_inbox_assign_action_rejects_missing_agent(db_session, crm_contact, crm_team, person):
    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )

    result = inbox_assign_conversation(
        db_session,
        conversation_id=str(conversation.id),
        agent_id=str(uuid.uuid4()),
        team_id=str(crm_team.id),
        assigned_by_id=str(person.id),
    )

    assert result.kind == "invalid_input"
    assert result.error_detail == "Selected agent does not exist or is inactive."


def test_inbox_assign_action_rejects_missing_team(db_session, crm_contact, crm_agent, person):
    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
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

    result = inbox_assign_conversation(
        db_session,
        conversation_id=str(conversation.id),
        agent_id=str(crm_agent.id),
        team_id=str(uuid.uuid4()),
        assigned_by_id=str(person.id),
    )

    assert result.kind == "invalid_input"
    assert result.error_detail == "Selected team does not exist or is inactive."


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


def test_effective_status_treats_stale_presence_as_away(db_session, crm_agent):
    presence = AgentPresence(
        agent_id=crm_agent.id,
        status=AgentPresenceStatus.online,
        manual_override_status=None,
        last_seen_at=datetime.now(UTC),
    )
    db_session.add(presence)
    db_session.commit()

    presence.last_seen_at = datetime.now(UTC) - timedelta(minutes=10)

    assert agent_presence_service.effective_status(presence) == AgentPresenceStatus.away


def test_effective_status_keeps_manual_offline_override(db_session, crm_agent):
    presence = AgentPresence(
        agent_id=crm_agent.id,
        status=AgentPresenceStatus.online,
        manual_override_status=AgentPresenceStatus.offline,
        last_seen_at=None,
    )
    db_session.add(presence)
    db_session.commit()

    assert agent_presence_service.effective_status(presence) == AgentPresenceStatus.offline


def test_conversation_assignment_unique_active_conversation(db_session, crm_contact, crm_team, crm_agent):
    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    second_person = Person(first_name="Second", last_name="Agent", email="second-agent@example.com")
    db_session.add(second_person)
    db_session.commit()
    db_session.refresh(second_person)

    second_agent = CrmAgent(person_id=second_person.id, title="Backup Agent")
    db_session.add(second_agent)
    db_session.commit()
    db_session.refresh(second_agent)

    db_session.add(
        ConversationAssignment(
            conversation_id=conversation.id,
            team_id=crm_team.id,
            agent_id=crm_agent.id,
            is_active=True,
        )
    )
    db_session.commit()

    db_session.add(
        ConversationAssignment(
            conversation_id=conversation.id,
            team_id=crm_team.id,
            agent_id=second_agent.id,
            is_active=True,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_manual_assignment_reopens_snoozed_conversation(db_session, crm_contact, crm_agent, crm_team, person):
    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    conversation.status = ConversationStatus.snoozed
    conversation.metadata_ = {
        "snooze": {
            "mode": "1h",
            "until_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "set_at": datetime.now(UTC).isoformat(),
            "set_by": str(person.id),
        }
    }
    db_session.add(
        AgentPresence(
            agent_id=crm_agent.id,
            status=AgentPresenceStatus.online,
            manual_override_status=None,
            last_seen_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    assignment = conversation_service.assign_conversation(
        db_session,
        conversation_id=str(conversation.id),
        agent_id=str(crm_agent.id),
        team_id=str(crm_team.id),
        assigned_by_id=str(person.id),
    )

    db_session.refresh(conversation)
    assert assignment is not None
    assert conversation.status == ConversationStatus.open
    assert conversation.metadata_ == {}


def _create_inbound_message(db_session, conversation):
    message = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.email,
        direction=MessageDirection.inbound,
        status=MessageStatus.received,
        body="Customer replied",
        received_at=datetime.now(UTC),
    )
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)
    return message


def test_inbound_routing_reassigns_unavailable_existing_assignee(db_session, crm_contact, crm_agent, crm_team, person):
    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
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

    existing_assignment = conversation_service.assign_conversation(
        db_session,
        conversation_id=str(conversation.id),
        agent_id=str(crm_agent.id),
        team_id=str(crm_team.id),
        assigned_by_id=str(person.id),
    )
    message = _create_inbound_message(db_session, conversation)
    db_session.add(
        CrmRoutingRule(
            team_id=crm_team.id,
            channel_type=ChannelType.email,
            rule_config={"strategy": "round_robin"},
            is_active=True,
        )
    )
    db_session.commit()

    presence = db_session.query(AgentPresence).filter(AgentPresence.agent_id == crm_agent.id).first()
    presence.manual_override_status = AgentPresenceStatus.offline
    presence.last_seen_at = datetime.now(UTC)
    db_session.commit()

    decision = apply_routing_rules(db_session, conversation=conversation, message=message)

    db_session.refresh(existing_assignment)
    active_assignment = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )

    assert decision is not None
    assert existing_assignment.is_active is False
    assert active_assignment is not None
    assert active_assignment.team_id == crm_team.id
    assert active_assignment.agent_id is None


def test_inbound_routing_keeps_available_existing_assignee(db_session, crm_contact, crm_agent, crm_team, person):
    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
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

    existing_assignment = conversation_service.assign_conversation(
        db_session,
        conversation_id=str(conversation.id),
        agent_id=str(crm_agent.id),
        team_id=str(crm_team.id),
        assigned_by_id=str(person.id),
    )
    message = _create_inbound_message(db_session, conversation)
    db_session.add(
        CrmRoutingRule(
            team_id=crm_team.id,
            channel_type=ChannelType.email,
            rule_config={"strategy": "round_robin"},
            is_active=True,
        )
    )
    db_session.commit()

    decision = apply_routing_rules(db_session, conversation=conversation, message=message)

    db_session.refresh(existing_assignment)
    active_assignments = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .all()
    )

    assert decision is None
    assert existing_assignment.is_active is True
    assert len(active_assignments) == 1
    assert active_assignments[0].id == existing_assignment.id


def test_manual_assignment_same_target_is_idempotent(db_session, crm_contact, crm_agent, crm_team, person):
    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
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

    first = conversation_service.assign_conversation(
        db_session,
        conversation_id=str(conversation.id),
        agent_id=str(crm_agent.id),
        team_id=str(crm_team.id),
        assigned_by_id=str(person.id),
    )
    second = conversation_service.assign_conversation(
        db_session,
        conversation_id=str(conversation.id),
        agent_id=str(crm_agent.id),
        team_id=str(crm_team.id),
        assigned_by_id=str(person.id),
    )

    assert first is not None
    assert second is not None
    assert second.id == first.id
    assignments = (
        db_session.query(ConversationAssignment).filter(ConversationAssignment.conversation_id == conversation.id).all()
    )
    assert len(assignments) == 1
    assert assignments[0].is_active is True


def test_manual_assignment_reactivates_existing_inactive_tuple(db_session, crm_contact, crm_agent, crm_team, person):
    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
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

    original = conversation_service.assign_conversation(
        db_session,
        conversation_id=str(conversation.id),
        agent_id=str(crm_agent.id),
        team_id=str(crm_team.id),
        assigned_by_id=str(person.id),
    )
    conversation_service.unassign_conversation(
        db_session,
        conversation_id=str(conversation.id),
    )
    reassigned = conversation_service.assign_conversation(
        db_session,
        conversation_id=str(conversation.id),
        agent_id=str(crm_agent.id),
        team_id=str(crm_team.id),
        assigned_by_id=str(person.id),
    )

    assert original is not None
    assert reassigned is not None
    assert reassigned.id == original.id
    active = (
        db_session.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .all()
    )
    assert len(active) == 1
    assert active[0].id == original.id
