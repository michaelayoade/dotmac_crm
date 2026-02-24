"""Tests for inbox filter-by-agent + date-range feature."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.models.crm.conversation import Conversation, ConversationAssignment
from app.models.crm.enums import ConversationStatus
from app.models.crm.team import CrmAgent
from app.models.person import Person
from app.services.crm.inbox.queries import list_inbox_conversations


def _unique_email() -> str:
    return f"agent-filter-{uuid.uuid4().hex[:8]}@example.com"


def _create_person(db_session, *, name: str = "Test") -> Person:
    person = Person(first_name=name, last_name="Contact", email=_unique_email())
    db_session.add(person)
    db_session.flush()
    return person


def _create_agent(db_session, person: Person) -> CrmAgent:
    agent = CrmAgent(person_id=person.id, title="Agent")
    db_session.add(agent)
    db_session.flush()
    return agent


def _create_conversation(db_session, contact: Person) -> Conversation:
    conv = Conversation(person_id=contact.id, status=ConversationStatus.open)
    db_session.add(conv)
    db_session.flush()
    return conv


def _assign(db_session, conversation: Conversation, agent: CrmAgent, *, assigned_at: datetime) -> ConversationAssignment:
    assignment = ConversationAssignment(
        conversation_id=conversation.id,
        agent_id=agent.id,
        assigned_at=assigned_at,
        is_active=True,
    )
    db_session.add(assignment)
    db_session.flush()
    return assignment


def _result_ids(results: list[tuple]) -> set[uuid.UUID]:
    return {row[0].id for row in results}


def test_agent_filter_returns_assigned_conversations(db_session):
    """assignment='agent' with a valid agent_id returns only that agent's conversations."""
    contact = _create_person(db_session, name="Contact")
    agent_person = _create_person(db_session, name="AgentA")
    other_person = _create_person(db_session, name="AgentB")
    agent_a = _create_agent(db_session, agent_person)
    agent_b = _create_agent(db_session, other_person)

    conv_a = _create_conversation(db_session, contact)
    conv_b = _create_conversation(db_session, contact)
    now = datetime.now(UTC)
    _assign(db_session, conv_a, agent_a, assigned_at=now)
    _assign(db_session, conv_b, agent_b, assigned_at=now)
    db_session.flush()

    results = list_inbox_conversations(
        db_session,
        assignment="agent",
        filter_agent_id=str(agent_a.id),
    )
    ids = _result_ids(results)
    assert conv_a.id in ids
    assert conv_b.id not in ids


def test_agent_filter_missing_agent_id_returns_empty(db_session):
    """assignment='agent' without filter_agent_id returns empty list."""
    contact = _create_person(db_session)
    _create_conversation(db_session, contact)
    db_session.flush()

    results = list_inbox_conversations(db_session, assignment="agent", filter_agent_id=None)
    assert results == []


def test_agent_filter_with_date_from_only(db_session):
    """assigned_from filters out conversations assigned before that date."""
    contact = _create_person(db_session)
    agent_person = _create_person(db_session, name="AgentDFrom")
    agent = _create_agent(db_session, agent_person)

    now = datetime.now(UTC)
    old = now - timedelta(days=30)
    recent = now - timedelta(days=2)

    conv_old = _create_conversation(db_session, contact)
    conv_recent = _create_conversation(db_session, contact)
    _assign(db_session, conv_old, agent, assigned_at=old)
    _assign(db_session, conv_recent, agent, assigned_at=recent)
    db_session.flush()

    cutoff = now - timedelta(days=7)
    results = list_inbox_conversations(
        db_session,
        assignment="agent",
        filter_agent_id=str(agent.id),
        assigned_from=cutoff,
    )
    ids = _result_ids(results)
    assert conv_recent.id in ids
    assert conv_old.id not in ids


def test_agent_filter_with_date_to_only(db_session):
    """assigned_to filters out conversations assigned after that date."""
    contact = _create_person(db_session)
    agent_person = _create_person(db_session, name="AgentDTo")
    agent = _create_agent(db_session, agent_person)

    now = datetime.now(UTC)
    old = now - timedelta(days=30)
    recent = now - timedelta(days=2)

    conv_old = _create_conversation(db_session, contact)
    conv_recent = _create_conversation(db_session, contact)
    _assign(db_session, conv_old, agent, assigned_at=old)
    _assign(db_session, conv_recent, agent, assigned_at=recent)
    db_session.flush()

    cutoff = now - timedelta(days=7)
    results = list_inbox_conversations(
        db_session,
        assignment="agent",
        filter_agent_id=str(agent.id),
        assigned_to=cutoff,
    )
    ids = _result_ids(results)
    assert conv_old.id in ids
    assert conv_recent.id not in ids


def test_agent_filter_with_date_range(db_session):
    """Both from + to narrows results to the window."""
    contact = _create_person(db_session)
    agent_person = _create_person(db_session, name="AgentRange")
    agent = _create_agent(db_session, agent_person)

    now = datetime.now(UTC)
    t1 = now - timedelta(days=60)
    t2 = now - timedelta(days=15)
    t3 = now - timedelta(days=2)

    conv1 = _create_conversation(db_session, contact)
    conv2 = _create_conversation(db_session, contact)
    conv3 = _create_conversation(db_session, contact)
    _assign(db_session, conv1, agent, assigned_at=t1)
    _assign(db_session, conv2, agent, assigned_at=t2)
    _assign(db_session, conv3, agent, assigned_at=t3)
    db_session.flush()

    results = list_inbox_conversations(
        db_session,
        assignment="agent",
        filter_agent_id=str(agent.id),
        assigned_from=now - timedelta(days=30),
        assigned_to=now - timedelta(days=5),
    )
    ids = _result_ids(results)
    assert conv2.id in ids
    assert conv1.id not in ids
    assert conv3.id not in ids


def test_existing_filters_unaffected(db_session):
    """The 'unassigned' filter still works after adding the 'agent' filter."""
    contact = _create_person(db_session)
    agent_person = _create_person(db_session, name="AgentExist")
    agent = _create_agent(db_session, agent_person)

    conv_assigned = _create_conversation(db_session, contact)
    conv_unassigned = _create_conversation(db_session, contact)
    _assign(db_session, conv_assigned, agent, assigned_at=datetime.now(UTC))
    db_session.flush()

    results = list_inbox_conversations(db_session, assignment="unassigned")
    ids = _result_ids(results)
    assert conv_unassigned.id in ids
    assert conv_assigned.id not in ids
