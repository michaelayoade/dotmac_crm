"""Tests for CRM conversation service."""

import uuid
from datetime import datetime, timezone, timedelta

import pytest
from fastapi import HTTPException

from app.models.crm.conversation import ConversationAssignment, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.schemas.crm.conversation import (
    ConversationCreate,
    ConversationUpdate,
    ConversationAssignmentCreate,
    ConversationTagCreate,
    MessageCreate,
    MessageUpdate,
    MessageAttachmentCreate,
)
from app.services.crm import conversation as conversation_service
from app.web.admin import crm as admin_crm


# =============================================================================
# Conversations CRUD Tests
# =============================================================================


def test_create_conversation(db_session, crm_contact):
    """Test creating a conversation."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(
            person_id=crm_contact.id,
            subject="Support Inquiry",
            status=ConversationStatus.open,
        ),
    )
    assert conv.person_id == crm_contact.id
    assert conv.subject == "Support Inquiry"
    assert conv.status == ConversationStatus.open
    assert conv.is_active is True


def test_create_conversation_inherits_person(db_session, person):
    """Test that conversation uses provided person_id."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=person.id),
    )
    assert conv.person_id == person.id


def test_create_conversation_person_not_found(db_session):
    """Test creating conversation with non-existent person raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        conversation_service.Conversations.create(
            db_session,
            ConversationCreate(person_id=uuid.uuid4()),
        )
    assert exc_info.value.status_code == 404
    assert "Person not found" in exc_info.value.detail


def test_get_conversation(db_session, crm_contact):
    """Test getting a conversation by ID."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    fetched = conversation_service.Conversations.get(db_session, str(conv.id))
    assert fetched.id == conv.id


def test_get_conversation_not_found(db_session):
    """Test getting non-existent conversation raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        conversation_service.Conversations.get(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404
    assert "Conversation not found" in exc_info.value.detail


def test_list_conversations(db_session, crm_contact):
    """Test listing conversations."""
    conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id, subject="Conv 1"),
    )
    conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id, subject="Conv 2"),
    )

    convs = conversation_service.Conversations.list(
        db_session,
        person_id=None,
        ticket_id=None,
        status=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(convs) >= 2


def test_list_conversations_filter_by_person(db_session, crm_contact):
    """Test listing conversations filtered by person_id."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )

    convs = conversation_service.Conversations.list(
        db_session,
        person_id=str(crm_contact.id),
        ticket_id=None,
        status=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(c.id == conv.id for c in convs)


def test_list_conversations_filter_by_status(db_session, crm_contact):
    """Test listing conversations filtered by status."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id, status=ConversationStatus.pending),
    )

    convs = conversation_service.Conversations.list(
        db_session,
        person_id=None,
        ticket_id=None,
        status="pending",
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert any(c.id == conv.id for c in convs)
    assert all(c.status == ConversationStatus.pending for c in convs)


def test_list_conversations_invalid_status(db_session):
    """Test listing conversations with invalid status raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        conversation_service.Conversations.list(
            db_session,
            person_id=None,
            ticket_id=None,
            status="invalid_status",
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
    assert exc_info.value.status_code == 400
    assert "Invalid status" in exc_info.value.detail


def test_list_conversations_order_by_last_message(db_session, crm_contact):
    """Test listing conversations ordered by last_message_at."""
    convs = conversation_service.Conversations.list(
        db_session,
        person_id=None,
        ticket_id=None,
        status=None,
        is_active=None,
        order_by="last_message_at",
        order_dir="desc",
        limit=10,
        offset=0,
    )
    assert isinstance(convs, list)


def test_update_conversation(db_session, crm_contact):
    """Test updating a conversation."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id, subject="Original"),
    )

    updated = conversation_service.Conversations.update(
        db_session,
        str(conv.id),
        ConversationUpdate(subject="Updated Subject", status=ConversationStatus.resolved),
    )
    assert updated.subject == "Updated Subject"
    assert updated.status == ConversationStatus.resolved


def test_update_conversation_not_found(db_session):
    """Test updating non-existent conversation raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        conversation_service.Conversations.update(
            db_session,
            str(uuid.uuid4()),
            ConversationUpdate(subject="New"),
        )
    assert exc_info.value.status_code == 404


def test_delete_conversation(db_session, crm_contact):
    """Test deleting (soft delete) a conversation."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    conversation_service.Conversations.delete(db_session, str(conv.id))
    db_session.refresh(conv)
    assert conv.is_active is False


def test_delete_conversation_not_found(db_session):
    """Test deleting non-existent conversation raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        conversation_service.Conversations.delete(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404


# =============================================================================
# Conversation Assignments Tests
# =============================================================================


def test_create_assignment_with_team(db_session, crm_contact, crm_team):
    """Test creating a conversation assignment with team."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )

    assignment = conversation_service.ConversationAssignments.create(
        db_session,
        ConversationAssignmentCreate(
            conversation_id=conv.id,
            team_id=crm_team.id,
        ),
    )
    assert assignment.conversation_id == conv.id
    assert assignment.team_id == crm_team.id


def test_create_assignment_with_agent(db_session, crm_contact, crm_agent):
    """Test creating a conversation assignment with agent."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )

    assignment = conversation_service.ConversationAssignments.create(
        db_session,
        ConversationAssignmentCreate(
            conversation_id=conv.id,
            agent_id=crm_agent.id,
        ),
    )
    assert assignment.agent_id == crm_agent.id


def test_create_assignment_deactivates_previous(db_session, crm_contact, crm_agent, crm_team):
    """New assignment should deactivate previous active assignment."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )

    first = conversation_service.ConversationAssignments.create(
        db_session,
        ConversationAssignmentCreate(
            conversation_id=conv.id,
            team_id=crm_team.id,
        ),
    )
    second = conversation_service.ConversationAssignments.create(
        db_session,
        ConversationAssignmentCreate(
            conversation_id=conv.id,
            agent_id=crm_agent.id,
        ),
    )

    db_session.refresh(first)
    db_session.refresh(second)
    assert first.is_active is False
    assert second.is_active is True


def test_create_assignment_requires_team_or_agent(db_session, crm_contact):
    """Test that assignment requires either team_id or agent_id."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )

    with pytest.raises(HTTPException) as exc_info:
        conversation_service.ConversationAssignments.create(
            db_session,
            ConversationAssignmentCreate(conversation_id=conv.id),
        )
    assert exc_info.value.status_code == 400
    assert "team_id or agent_id" in exc_info.value.detail


def test_create_assignment_conversation_not_found(db_session, crm_team):
    """Test creating assignment for non-existent conversation raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        conversation_service.ConversationAssignments.create(
            db_session,
            ConversationAssignmentCreate(
                conversation_id=uuid.uuid4(),
                team_id=crm_team.id,
            ),
        )
    assert exc_info.value.status_code == 404


def test_list_assignments(db_session, crm_contact, crm_team):
    """Test listing conversation assignments."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    conversation_service.ConversationAssignments.create(
        db_session,
        ConversationAssignmentCreate(conversation_id=conv.id, team_id=crm_team.id),
    )

    assignments = conversation_service.ConversationAssignments.list(
        db_session,
        conversation_id=str(conv.id),
        team_id=None,
        agent_id=None,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(assignments) >= 1


# =============================================================================
# Conversation Tags Tests
# =============================================================================


def test_create_tag(db_session, crm_contact):
    """Test creating a conversation tag."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )

    tag = conversation_service.ConversationTags.create(
        db_session,
        ConversationTagCreate(conversation_id=conv.id, tag="urgent"),
    )
    assert tag.conversation_id == conv.id
    assert tag.tag == "urgent"


def test_create_tag_conversation_not_found(db_session):
    """Test creating tag for non-existent conversation raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        conversation_service.ConversationTags.create(
            db_session,
            ConversationTagCreate(conversation_id=uuid.uuid4(), tag="test"),
        )
    assert exc_info.value.status_code == 404


def test_list_tags(db_session, crm_contact):
    """Test listing conversation tags."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    conversation_service.ConversationTags.create(
        db_session,
        ConversationTagCreate(conversation_id=conv.id, tag="billing"),
    )

    tags = conversation_service.ConversationTags.list(
        db_session,
        conversation_id=str(conv.id),
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(tags) >= 1


def test_list_tags_order_by_tag(db_session, crm_contact):
    """Test listing tags ordered by tag name."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    conversation_service.ConversationTags.create(
        db_session,
        ConversationTagCreate(conversation_id=conv.id, tag="zzz"),
    )
    conversation_service.ConversationTags.create(
        db_session,
        ConversationTagCreate(conversation_id=conv.id, tag="aaa"),
    )

    tags = conversation_service.ConversationTags.list(
        db_session,
        conversation_id=str(conv.id),
        order_by="tag",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert tags[0].tag == "aaa"


# =============================================================================
# Messages Tests
# =============================================================================


def test_create_message(db_session, crm_contact):
    """Test creating a message."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )

    message = conversation_service.Messages.create(
        db_session,
        MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.inbound,
            body="Hello, I need help!",
        ),
    )
    assert message.conversation_id == conv.id
    assert message.channel_type == ChannelType.email
    assert message.direction == MessageDirection.inbound
    assert message.body == "Hello, I need help!"


def test_create_message_updates_last_message_at(db_session, crm_contact):
    """Test that creating message updates conversation.last_message_at."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    original_last = conv.last_message_at

    conversation_service.Messages.create(
        db_session,
        MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.inbound,
        ),
    )
    db_session.refresh(conv)
    assert conv.last_message_at != original_last
    assert conv.last_message_at is not None


def test_create_message_conversation_not_found(db_session):
    """Test creating message for non-existent conversation raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        conversation_service.Messages.create(
            db_session,
            MessageCreate(
                conversation_id=uuid.uuid4(),
                channel_type=ChannelType.email,
                direction=MessageDirection.inbound,
            ),
        )
    assert exc_info.value.status_code == 404


def test_get_message(db_session, crm_contact):
    """Test getting a message by ID."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    message = conversation_service.Messages.create(
        db_session,
        MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.inbound,
        ),
    )

    fetched = conversation_service.Messages.get(db_session, str(message.id))
    assert fetched.id == message.id


def test_get_message_not_found(db_session):
    """Test getting non-existent message raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        conversation_service.Messages.get(db_session, str(uuid.uuid4()))
    assert exc_info.value.status_code == 404


def test_list_messages(db_session, crm_contact):
    """Test listing messages."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    conversation_service.Messages.create(
        db_session,
        MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.inbound,
        ),
    )

    messages = conversation_service.Messages.list(
        db_session,
        conversation_id=str(conv.id),
        channel_type=None,
        direction=None,
        status=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert len(messages) >= 1


def test_list_messages_filter_by_channel_type(db_session, crm_contact):
    """Test listing messages filtered by channel type."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    conversation_service.Messages.create(
        db_session,
        MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.whatsapp,
            direction=MessageDirection.inbound,
        ),
    )

    messages = conversation_service.Messages.list(
        db_session,
        conversation_id=str(conv.id),
        channel_type="whatsapp",
        direction=None,
        status=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert all(m.channel_type == ChannelType.whatsapp for m in messages)


def test_list_messages_filter_by_direction(db_session, crm_contact):
    """Test listing messages filtered by direction."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    conversation_service.Messages.create(
        db_session,
        MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.outbound,
        ),
    )

    messages = conversation_service.Messages.list(
        db_session,
        conversation_id=None,
        channel_type=None,
        direction="outbound",
        status=None,
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert all(m.direction == MessageDirection.outbound for m in messages)


def test_list_messages_filter_by_status(db_session, crm_contact):
    """Test listing messages filtered by status."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    conversation_service.Messages.create(
        db_session,
        MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
        ),
    )

    messages = conversation_service.Messages.list(
        db_session,
        conversation_id=None,
        channel_type=None,
        direction=None,
        status="sent",
        order_by="created_at",
        order_dir="asc",
        limit=10,
        offset=0,
    )
    assert all(m.status == MessageStatus.sent for m in messages)


def test_list_messages_invalid_channel_type(db_session):
    """Test listing messages with invalid channel type raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        conversation_service.Messages.list(
            db_session,
            conversation_id=None,
            channel_type="invalid",
            direction=None,
            status=None,
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
    assert exc_info.value.status_code == 400


def test_list_messages_invalid_direction(db_session):
    """Test listing messages with invalid direction raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        conversation_service.Messages.list(
            db_session,
            conversation_id=None,
            channel_type=None,
            direction="invalid",
            status=None,
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
    assert exc_info.value.status_code == 400


def test_list_messages_invalid_status(db_session):
    """Test listing messages with invalid status raises 400."""
    with pytest.raises(HTTPException) as exc_info:
        conversation_service.Messages.list(
            db_session,
            conversation_id=None,
            channel_type=None,
            direction=None,
            status="invalid",
            order_by="created_at",
            order_dir="asc",
            limit=10,
            offset=0,
        )
    assert exc_info.value.status_code == 400


def test_update_message(db_session, crm_contact):
    """Test updating a message."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    message = conversation_service.Messages.create(
        db_session,
        MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.outbound,
            status=MessageStatus.queued,
        ),
    )

    updated = conversation_service.Messages.update(
        db_session,
        str(message.id),
        MessageUpdate(status=MessageStatus.sent),
    )
    assert updated.status == MessageStatus.sent


def test_update_message_not_found(db_session):
    """Test updating non-existent message raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        conversation_service.Messages.update(
            db_session,
            str(uuid.uuid4()),
            MessageUpdate(status=MessageStatus.sent),
        )
    assert exc_info.value.status_code == 404


# =============================================================================
# Message Attachments Tests
# =============================================================================


def test_create_attachment(db_session, crm_contact):
    """Test creating a message attachment."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    message = conversation_service.Messages.create(
        db_session,
        MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.inbound,
        ),
    )

    attachment = conversation_service.MessageAttachments.create(
        db_session,
        MessageAttachmentCreate(
            message_id=message.id,
            file_name="document.pdf",
            mime_type="application/pdf",
            file_size=1024,
        ),
    )
    assert attachment.message_id == message.id
    assert attachment.file_name == "document.pdf"
    assert attachment.mime_type == "application/pdf"


def test_create_attachment_message_not_found(db_session):
    """Test creating attachment for non-existent message raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        conversation_service.MessageAttachments.create(
            db_session,
            MessageAttachmentCreate(message_id=uuid.uuid4(), file_name="test.txt"),
        )
    assert exc_info.value.status_code == 404


# =============================================================================
# Helper Function Tests
# =============================================================================


def test_resolve_open_conversation(db_session, crm_contact):
    """Test resolving open conversation for a contact."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id, status=ConversationStatus.open),
    )

    resolved = conversation_service.resolve_open_conversation(db_session, str(crm_contact.id))
    assert resolved is not None
    assert resolved.id == conv.id


def test_resolve_open_conversation_pending(db_session, crm_contact):
    """Test resolving pending conversation for a contact."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id, status=ConversationStatus.pending),
    )

    resolved = conversation_service.resolve_open_conversation(db_session, str(crm_contact.id))
    assert resolved is not None
    assert resolved.id == conv.id


def test_resolve_open_conversation_none(db_session, crm_contact):
    """Test resolving returns None when no open conversation."""
    # Create closed conversation
    conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id, status=ConversationStatus.resolved),
    )

    resolved = conversation_service.resolve_open_conversation(db_session, str(crm_contact.id))
    assert resolved is None


def test_resolve_open_conversation_for_channel_single_channel(db_session, crm_contact):
    """Test resolving an open conversation for matching channel."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id, status=ConversationStatus.open),
    )
    conversation_service.Messages.create(
        db_session,
        MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.inbound,
        ),
    )

    resolved = conversation_service.resolve_open_conversation_for_channel(
        db_session,
        str(crm_contact.id),
        ChannelType.email,
    )
    assert resolved is not None
    assert resolved.id == conv.id


def test_resolve_open_conversation_for_channel_skips_mixed_channels(
    db_session, crm_contact
):
    """Test resolving skips conversations with other channel types."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id, status=ConversationStatus.open),
    )
    conversation_service.Messages.create(
        db_session,
        MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.whatsapp,
            direction=MessageDirection.inbound,
        ),
    )
    conversation_service.Messages.create(
        db_session,
        MessageCreate(
            conversation_id=conv.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.inbound,
        ),
    )

    resolved = conversation_service.resolve_open_conversation_for_channel(
        db_session,
        str(crm_contact.id),
        ChannelType.email,
    )
    assert resolved is None


def test_resolve_conversation_contact_prefers_inbound_channel(
    db_session, crm_contact, crm_contact_channel
):
    """Test resolve_conversation_contact uses last inbound message for channel."""
    from app.models.person import PersonChannel, ChannelType as PersonChannelType

    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id, status=ConversationStatus.open),
    )
    conversation_service.Messages.create(
        db_session,
        MessageCreate(
            conversation_id=conv.id,
            person_channel_id=crm_contact_channel.id,
            channel_type=ChannelType.email,
            direction=MessageDirection.inbound,
            status=MessageStatus.received,
            body="Inbound email",
            received_at=datetime.now(timezone.utc),
        ),
    )
    conversation_service.Messages.create(
        db_session,
        MessageCreate(
            conversation_id=conv.id,
            person_channel_id=crm_contact_channel.id,
            channel_type=ChannelType.whatsapp,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body="Outbound WhatsApp",
            sent_at=datetime.now(timezone.utc),
        ),
    )

    conversation_service.resolve_conversation_contact(
        db_session,
        conversation_id=str(conv.id),
        person_id=str(crm_contact.id),
    )

    whatsapp_channel = (
        db_session.query(PersonChannel)
        .filter(PersonChannel.person_id == crm_contact.id)
        .filter(PersonChannel.channel_type == PersonChannelType.whatsapp)
        .filter(PersonChannel.address == crm_contact.email)
        .first()
    )
    assert whatsapp_channel is None


def test_resolve_person_channel(db_session, crm_contact, crm_contact_channel):
    """Test resolving person channel."""
    resolved = conversation_service.resolve_person_channel(
        db_session, str(crm_contact.id), ChannelType.email
    )
    assert resolved is not None
    assert resolved.id == crm_contact_channel.id


def test_resolve_person_channel_none(db_session, crm_contact):
    """Test resolving returns None when no matching channel."""
    resolved = conversation_service.resolve_person_channel(
        db_session, str(crm_contact.id), ChannelType.whatsapp
    )
    assert resolved is None


def test_mark_conversation_read_respects_assignment_and_last_seen(db_session, crm_contact, crm_agent):
    """Read mark should respect assignment and last seen timestamp."""
    conv = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    assignment = ConversationAssignment(
        conversation_id=conv.id,
        agent_id=crm_agent.id,
        is_active=True,
        assigned_at=datetime.now(timezone.utc),
    )
    db_session.add(assignment)

    base_time = datetime.now(timezone.utc)
    first = Message(
        conversation_id=conv.id,
        channel_type=ChannelType.email,
        direction=MessageDirection.inbound,
        status=MessageStatus.received,
        received_at=base_time - timedelta(minutes=5),
    )
    second = Message(
        conversation_id=conv.id,
        channel_type=ChannelType.email,
        direction=MessageDirection.inbound,
        status=MessageStatus.received,
        received_at=base_time + timedelta(minutes=5),
    )
    db_session.add_all([first, second])
    db_session.commit()

    admin_crm._mark_conversation_read(
        db_session,
        str(conv.id),
        str(crm_agent.person_id),
        first.received_at,
    )

    db_session.refresh(first)
    db_session.refresh(second)
    assert first.read_at is not None
    assert second.read_at is None
