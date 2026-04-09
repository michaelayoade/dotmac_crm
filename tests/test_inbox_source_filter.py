"""Tests for inbox source filtering on conversation queries."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.models.integration import IntegrationTarget, IntegrationTargetType
from app.models.person import Person
from app.services.crm.inbox.queries import list_inbox_conversations


def _email() -> str:
    return f"inbox-source-{uuid.uuid4().hex[:10]}@example.com"


def _create_contact(db_session, name: str) -> Person:
    person = Person(first_name=name, last_name="Contact", email=_email())
    db_session.add(person)
    db_session.flush()
    return person


def _create_inbox_target(db_session, *, name: str, connector_type: ConnectorType) -> IntegrationTarget:
    config = ConnectorConfig(name=f"{name} Config", connector_type=connector_type, is_active=True)
    db_session.add(config)
    db_session.flush()
    target = IntegrationTarget(
        name=name,
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
        is_active=True,
    )
    db_session.add(target)
    db_session.flush()
    return target


def _create_conversation_for_target(db_session, *, contact: Person, target: IntegrationTarget) -> Conversation:
    conversation = Conversation(person_id=contact.id, status=ConversationStatus.open)
    db_session.add(conversation)
    db_session.flush()
    message = Message(
        conversation_id=conversation.id,
        channel_type=ChannelType.email,
        direction=MessageDirection.inbound,
        status=MessageStatus.received,
        body=f"Message for {target.name}",
        received_at=datetime.now(UTC),
        channel_target_id=target.id,
    )
    db_session.add(message)
    db_session.flush()
    return conversation


def test_list_inbox_conversations_filters_by_inbox_id(db_session):
    """Selecting an inbox id should only return conversations from that inbox."""
    alpha_target = _create_inbox_target(db_session, name="Alpha Desk", connector_type=ConnectorType.email)
    beta_target = _create_inbox_target(db_session, name="Beta Desk", connector_type=ConnectorType.whatsapp)
    alpha_contact = _create_contact(db_session, "Alpha")
    beta_contact = _create_contact(db_session, "Beta")

    alpha_conversation = _create_conversation_for_target(db_session, contact=alpha_contact, target=alpha_target)
    beta_conversation = _create_conversation_for_target(db_session, contact=beta_contact, target=beta_target)
    db_session.commit()

    results = list_inbox_conversations(db_session, channel_target_id=str(alpha_target.id))
    result_ids = {row[0].id for row in results}

    assert alpha_conversation.id in result_ids
    assert beta_conversation.id not in result_ids
