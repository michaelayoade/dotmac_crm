"""Tests for message ordering using platform timestamps."""

from datetime import datetime, timedelta, timezone
import uuid

from app.models.crm.conversation import Message
from app.models.crm.enums import ChannelType, MessageDirection, MessageStatus
from app.models.person import PersonChannel, ChannelType as PersonChannelType
from app.schemas.crm.conversation import ConversationCreate
from app.services.crm import conversation as conversation_service
from app.services.crm import inbox as inbox_service
from app.services.crm import reports as reports_service
from app.web.admin import crm as admin_crm
from app.web.admin import tickets as admin_tickets


def _add_message(
    db_session,
    conversation,
    person_channel,
    channel_type,
    direction,
    body,
    received_at=None,
    sent_at=None,
    created_at=None,
):
    message = Message(
        conversation_id=conversation.id,
        person_channel_id=person_channel.id if person_channel else None,
        channel_type=channel_type,
        direction=direction,
        status=MessageStatus.received if direction == MessageDirection.inbound else MessageStatus.sent,
        body=body,
        external_id=str(uuid.uuid4()),
        received_at=received_at,
        sent_at=sent_at,
        created_at=created_at,
    )
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)
    return message


def test_last_inbound_message_uses_received_at(db_session, crm_contact, crm_contact_channel):
    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    now = datetime.now(timezone.utc)
    older_received = now - timedelta(minutes=5)
    newer_received = now - timedelta(minutes=1)

    _add_message(
        db_session,
        conversation,
        crm_contact_channel,
        ChannelType.email,
        MessageDirection.inbound,
        "older",
        received_at=older_received,
        created_at=now,
    )
    newest = _add_message(
        db_session,
        conversation,
        crm_contact_channel,
        ChannelType.email,
        MessageDirection.inbound,
        "newer",
        received_at=newer_received,
        created_at=now - timedelta(minutes=10),
    )

    last_inbound = inbox_service._get_last_inbound_message(db_session, conversation.id)
    assert last_inbound.id == newest.id


def test_messages_list_orders_by_received_at(db_session, crm_contact, crm_contact_channel):
    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    now = datetime.now(timezone.utc)
    older_received = now - timedelta(hours=2)
    newer_received = now - timedelta(hours=1)

    older = _add_message(
        db_session,
        conversation,
        crm_contact_channel,
        ChannelType.email,
        MessageDirection.inbound,
        "older",
        received_at=older_received,
        created_at=now,
    )
    newer = _add_message(
        db_session,
        conversation,
        crm_contact_channel,
        ChannelType.email,
        MessageDirection.inbound,
        "newer",
        received_at=newer_received,
        created_at=now - timedelta(days=1),
    )

    messages = conversation_service.Messages.list(
        db_session,
        conversation_id=str(conversation.id),
        channel_type=None,
        direction=None,
        status=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert [msg.id for msg in messages] == [older.id, newer.id]


def test_format_conversation_uses_received_at_for_latest(db_session, crm_contact):
    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    email_channel = PersonChannel(
        person_id=crm_contact.id,
        channel_type=PersonChannelType.email,
        address=crm_contact.email,
        is_primary=True,
    )
    whatsapp_channel = PersonChannel(
        person_id=crm_contact.id,
        channel_type=PersonChannelType.whatsapp,
        address="+15551234567",
        is_primary=False,
    )
    db_session.add_all([email_channel, whatsapp_channel])
    db_session.commit()

    now = datetime.now(timezone.utc)
    _add_message(
        db_session,
        conversation,
        email_channel,
        ChannelType.email,
        MessageDirection.inbound,
        "email",
        received_at=now - timedelta(minutes=10),
        created_at=now,
    )
    _add_message(
        db_session,
        conversation,
        whatsapp_channel,
        ChannelType.whatsapp,
        MessageDirection.inbound,
        "whatsapp",
        received_at=now - timedelta(minutes=1),
        created_at=now - timedelta(hours=1),
    )

    formatted = admin_crm._format_conversation_for_template(conversation, db_session)
    assert formatted["channel"] == "whatsapp"


def test_ticket_inbound_bounds_use_received_at(db_session, crm_contact, crm_contact_channel):
    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    now = datetime.now(timezone.utc)
    oldest_received = now - timedelta(days=2)
    newest_received = now - timedelta(days=1)

    oldest = _add_message(
        db_session,
        conversation,
        crm_contact_channel,
        ChannelType.email,
        MessageDirection.inbound,
        "oldest",
        received_at=oldest_received,
        created_at=now,
    )
    newest = _add_message(
        db_session,
        conversation,
        crm_contact_channel,
        ChannelType.email,
        MessageDirection.inbound,
        "newest",
        received_at=newest_received,
        created_at=now - timedelta(days=3),
    )

    last_inbound, first_inbound = admin_tickets._get_inbound_message_bounds(
        db_session, conversation.id
    )
    assert last_inbound.id == newest.id
    assert first_inbound.id == oldest.id


def test_inbox_kpis_use_received_at_for_response_time(db_session, crm_contact, crm_contact_channel):
    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    now = datetime.now(timezone.utc)
    inbound_received = now - timedelta(minutes=15)
    outbound_sent = now - timedelta(minutes=10)

    _add_message(
        db_session,
        conversation,
        crm_contact_channel,
        ChannelType.email,
        MessageDirection.inbound,
        "inbound",
        received_at=inbound_received,
        created_at=now,
    )
    _add_message(
        db_session,
        conversation,
        crm_contact_channel,
        ChannelType.email,
        MessageDirection.outbound,
        "outbound",
        sent_at=outbound_sent,
        created_at=now - timedelta(minutes=30),
    )

    metrics = reports_service.inbox_kpis(
        db_session,
        start_at=None,
        end_at=None,
        channel_type="email",
        agent_id=None,
        team_id=None,
    )
    assert metrics["avg_response_minutes"] == 5
