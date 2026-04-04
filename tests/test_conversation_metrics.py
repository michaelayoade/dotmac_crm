"""Tests for conversation metric population (first_response_at, response_time_seconds)."""

import uuid

from app.models.crm.conversation import Conversation
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.models.crm.team import CrmAgent
from app.models.person import Person
from app.schemas.crm.conversation import MessageCreate
from app.services.crm.conversations.service import Messages


def _make_person(db) -> Person:
    person = Person(
        first_name="Test",
        last_name="User",
        display_name="Test User",
        email=f"{uuid.uuid4().hex}@test.com",
        is_active=True,
    )
    db.add(person)
    db.flush()
    return person


def _make_conversation(db, person_id) -> Conversation:
    conv = Conversation(
        person_id=person_id,
        status=ConversationStatus.open,
    )
    db.add(conv)
    db.flush()
    return conv


def _make_agent(db, person_id) -> CrmAgent:
    agent = CrmAgent(
        person_id=person_id,
        is_active=True,
    )
    db.add(agent)
    db.flush()
    return agent


class TestFirstResponseAt:
    def test_set_on_first_agent_outbound_message(self, db_session):
        person = _make_person(db_session)
        agent_person = _make_person(db_session)
        _make_agent(db_session, agent_person.id)
        conv = _make_conversation(db_session, person.id)

        payload = MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body="Hello!",
            author_id=agent_person.id,
        )
        Messages.create(db_session, payload)
        db_session.refresh(conv)

        assert conv.first_response_at is not None
        assert conv.response_time_seconds is not None
        assert conv.response_time_seconds >= 0

    def test_not_set_on_inbound_message(self, db_session):
        person = _make_person(db_session)
        conv = _make_conversation(db_session, person.id)

        payload = MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.inbound,
            status=MessageStatus.received,
            body="Hi there",
            author_id=person.id,
        )
        Messages.create(db_session, payload)
        db_session.refresh(conv)

        assert conv.first_response_at is None
        assert conv.response_time_seconds is None

    def test_not_set_on_non_agent_outbound(self, db_session):
        person = _make_person(db_session)
        non_agent_person = _make_person(db_session)
        conv = _make_conversation(db_session, person.id)

        payload = MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body="Not an agent",
            author_id=non_agent_person.id,
        )
        Messages.create(db_session, payload)
        db_session.refresh(conv)

        assert conv.first_response_at is None
        assert conv.response_time_seconds is None

    def test_not_overwritten_on_second_outbound(self, db_session):
        person = _make_person(db_session)
        agent_person = _make_person(db_session)
        _make_agent(db_session, agent_person.id)
        conv = _make_conversation(db_session, person.id)

        payload1 = MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body="First reply",
            author_id=agent_person.id,
        )
        Messages.create(db_session, payload1)
        db_session.refresh(conv)
        first_response_at = conv.first_response_at
        first_response_seconds = conv.response_time_seconds

        assert first_response_at is not None

        payload2 = MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body="Second reply",
            author_id=agent_person.id,
        )
        Messages.create(db_session, payload2)
        db_session.refresh(conv)

        assert conv.first_response_at == first_response_at
        assert conv.response_time_seconds == first_response_seconds

    def test_no_author_id_does_not_set(self, db_session):
        person = _make_person(db_session)
        conv = _make_conversation(db_session, person.id)

        payload = MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body="System message",
            author_id=None,
        )
        Messages.create(db_session, payload)
        db_session.refresh(conv)

        assert conv.first_response_at is None
        assert conv.response_time_seconds is None


class TestResolvedAt:
    def test_set_on_resolve(self, db_session):
        from app.services.crm.inbox.conversation_status import update_conversation_status

        person = _make_person(db_session)
        conv = _make_conversation(db_session, person.id)

        result = update_conversation_status(
            db_session,
            conversation_id=str(conv.id),
            new_status="resolved",
        )
        db_session.refresh(conv)

        assert result.kind == "updated"
        assert conv.resolved_at is not None
        assert conv.resolution_time_seconds is not None
        assert conv.resolution_time_seconds >= 0

    def test_reopen_from_resolved_is_blocked(self, db_session):
        from app.services.crm.inbox.conversation_status import update_conversation_status

        person = _make_person(db_session)
        conv = _make_conversation(db_session, person.id)

        # First resolve
        update_conversation_status(
            db_session,
            conversation_id=str(conv.id),
            new_status="resolved",
        )
        db_session.refresh(conv)
        assert conv.resolved_at is not None
        assert conv.resolution_time_seconds is not None

        # Then reopen
        result = update_conversation_status(
            db_session,
            conversation_id=str(conv.id),
            new_status="open",
        )
        db_session.refresh(conv)

        assert result.kind == "invalid_transition"
        assert conv.resolved_at is not None
        assert conv.resolution_time_seconds is not None
