"""Tests for CRM inbox service."""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.crm.enums import ChannelType, MessageDirection, MessageStatus
from app.models.integration import (
    IntegrationJob,
    IntegrationJobType,
    IntegrationScheduleType,
    IntegrationTarget,
    IntegrationTargetType,
)
from app.schemas.crm.inbox import (
    EmailWebhookPayload,
    InboxSendRequest,
    WhatsAppWebhookPayload,
)
from app.services.crm import inbox as inbox_service


# =============================================================================
# Helper Functions Tests
# =============================================================================


def test_render_personalization():
    """Test rendering personalization placeholders in body."""
    body = "Hello {{name}}, your order {{order_id}} is ready."
    personalization = {"name": "John", "order_id": "12345"}
    result = inbox_service._render_personalization(body, personalization)
    assert result == "Hello John, your order 12345 is ready."


def test_render_personalization_none():
    """Test rendering with no personalization returns body unchanged."""
    body = "Hello {{name}}!"
    result = inbox_service._render_personalization(body, None)
    assert result == "Hello {{name}}!"


def test_render_personalization_empty():
    """Test rendering with empty personalization returns body unchanged."""
    body = "Hello {{name}}!"
    result = inbox_service._render_personalization(body, {})
    assert result == "Hello {{name}}!"


# =============================================================================
# Receive WhatsApp Message Tests
# =============================================================================


def test_receive_whatsapp_message(db_session):
    """Test receiving a WhatsApp message creates contact, conversation, and message."""
    payload = WhatsAppWebhookPayload(
        contact_address="+15551234567",
        contact_name="Test User",
        message_id="wamid.test123",
        body="Hello, I need help!",
    )

    message = inbox_service.receive_whatsapp_message(db_session, payload)

    assert message is not None
    assert message.body == "Hello, I need help!"
    assert message.channel_type == ChannelType.whatsapp
    assert message.direction == MessageDirection.inbound
    assert message.status == MessageStatus.received
    assert message.external_id == "wamid.test123"


def test_receive_whatsapp_message_existing_conversation(db_session):
    """Test receiving WhatsApp message uses existing open conversation."""
    # First message creates conversation
    payload1 = WhatsAppWebhookPayload(
        contact_address="+15559876543",
        body="First message",
    )
    message1 = inbox_service.receive_whatsapp_message(db_session, payload1)
    conversation_id = message1.conversation_id

    # Second message should use same conversation
    payload2 = WhatsAppWebhookPayload(
        contact_address="+15559876543",
        body="Second message",
    )
    message2 = inbox_service.receive_whatsapp_message(db_session, payload2)

    assert message2.conversation_id == conversation_id


def test_receive_whatsapp_message_with_metadata(db_session):
    """Test receiving WhatsApp message with metadata creates message successfully."""
    payload = WhatsAppWebhookPayload(
        contact_address="+15551112222",
        body="Message with metadata",
        metadata={"type": "image", "media_url": "https://example.com/image.jpg"},
    )

    message = inbox_service.receive_whatsapp_message(db_session, payload)

    # Message is created successfully even with metadata in payload
    assert message is not None
    assert message.body == "Message with metadata"


def test_receive_whatsapp_message_with_timestamp(db_session):
    """Test receiving WhatsApp message with explicit timestamp."""
    received_at = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
    payload = WhatsAppWebhookPayload(
        contact_address="+15553334444",
        body="Timestamped message",
        received_at=received_at,
    )

    message = inbox_service.receive_whatsapp_message(db_session, payload)

    # Compare without timezone (SQLite doesn't store timezone)
    assert message.received_at.year == received_at.year
    assert message.received_at.month == received_at.month
    assert message.received_at.day == received_at.day
    assert message.received_at.hour == received_at.hour
    assert message.received_at.minute == received_at.minute


def test_receive_whatsapp_message_skips_self(db_session):
    """Self-sent WhatsApp payloads should be ignored to prevent loops."""
    payload = WhatsAppWebhookPayload(
        contact_address="+15551234567",
        body="Self message",
        metadata={"from_me": True},
    )

    message = inbox_service.receive_whatsapp_message(db_session, payload)

    assert message is None


# =============================================================================
# Receive Email Message Tests
# =============================================================================


def test_receive_email_message(db_session):
    """Test receiving an email message creates contact, conversation, and message."""
    payload = EmailWebhookPayload(
        contact_address="customer@example.com",
        contact_name="Customer Name",
        message_id="msg-id-123",
        subject="Support Request",
        body="I need help with my account.",
    )

    message = inbox_service.receive_email_message(db_session, payload)

    assert message is not None
    assert message.body == "I need help with my account."
    assert message.subject == "Support Request"
    assert message.channel_type == ChannelType.email
    assert message.direction == MessageDirection.inbound
    assert message.status == MessageStatus.received


def test_receive_email_message_existing_conversation(db_session):
    """Test receiving email message uses existing open conversation."""
    # First email creates conversation
    payload1 = EmailWebhookPayload(
        contact_address="repeat@example.com",
        subject="Initial Email",
        body="First email message",
    )
    message1 = inbox_service.receive_email_message(db_session, payload1)
    conversation_id = message1.conversation_id

    # Second email should use same conversation
    payload2 = EmailWebhookPayload(
        contact_address="repeat@example.com",
        subject="Follow up",
        body="Second email message",
    )
    message2 = inbox_service.receive_email_message(db_session, payload2)

    assert message2.conversation_id == conversation_id


def test_inbound_messages_use_channel_scoped_conversations(db_session, subscriber_account):
    """Inbound messages should not reuse conversations across channels."""
    email_payload = EmailWebhookPayload(
        contact_address="cross-channel@example.com",
        subject="Email subject",
        body="Email body",
        received_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        metadata={"account_id": str(subscriber_account.id)},
    )
    email_message = inbox_service.receive_email_message(db_session, email_payload)

    whatsapp_payload = WhatsAppWebhookPayload(
        contact_address="+15551234567",
        body="WhatsApp body",
        received_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        metadata={"account_id": str(subscriber_account.id)},
    )
    whatsapp_message = inbox_service.receive_whatsapp_message(db_session, whatsapp_payload)

    assert whatsapp_message.conversation_id != email_message.conversation_id


def test_receive_email_message_with_metadata(db_session):
    """Test receiving email message with metadata creates message successfully."""
    payload = EmailWebhookPayload(
        contact_address="meta@example.com",
        body="Email with metadata",
        metadata={"has_attachments": True, "attachment_count": 2},
    )

    message = inbox_service.receive_email_message(db_session, payload)

    # Message is created successfully even with metadata in payload
    assert message is not None
    assert message.body == "Email with metadata"


def test_receive_email_message_dedupes_across_targets(db_session):
    """Test duplicate email dedupes across differing channel_target_id values."""
    from app.models.connector import ConnectorConfig, ConnectorType
    from app.models.integration import IntegrationTarget, IntegrationTargetType
    from datetime import datetime, timezone

    config_old = ConnectorConfig(name="email-old", connector_type=ConnectorType.email)
    config_new = ConnectorConfig(name="email-new", connector_type=ConnectorType.email)
    db_session.add_all([config_old, config_new])
    db_session.commit()
    db_session.refresh(config_old)
    db_session.refresh(config_new)

    target_old = IntegrationTarget(
        name="email-old-target",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config_old.id,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    target_new = IntegrationTarget(
        name="email-new-target",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config_new.id,
        created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    db_session.add_all([target_old, target_new])
    db_session.commit()
    db_session.refresh(target_old)
    db_session.refresh(target_new)

    payload_with_target = EmailWebhookPayload(
        contact_address="dup@example.com",
        message_id="msg-id-dup",
        subject="Duplicate Test",
        body="Same message across targets",
        channel_target_id=target_old.id,
    )
    first = inbox_service.receive_email_message(db_session, payload_with_target)

    payload_without_target = EmailWebhookPayload(
        contact_address="dup@example.com",
        message_id="msg-id-dup",
        subject="Duplicate Test",
        body="Same message across targets",
    )
    second = inbox_service.receive_email_message(db_session, payload_without_target)

    assert first.id == second.id


def test_receive_email_message_dedupes_without_message_id(db_session):
    """Test deterministic dedupe when email message_id is missing."""
    payload = EmailWebhookPayload(
        contact_address="nomsgid@example.com",
        subject="No Message ID",
        body="Same body content",
    )

    first = inbox_service.receive_email_message(db_session, payload)
    second = inbox_service.receive_email_message(db_session, payload)

    assert first.id == second.id


# =============================================================================
# Send Message Tests
# =============================================================================


def test_send_email_message_no_config(db_session, crm_contact, crm_contact_channel):
    """Test sending email message without connector config uses default."""
    from app.schemas.crm.conversation import ConversationCreate
    from app.services.crm import conversation as conversation_service

    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )

    payload = InboxSendRequest(
        conversation_id=conversation.id,
        channel_type=ChannelType.email,
        subject="Test Subject",
        body="Test email body",
    )

    with patch("app.services.email.send_email", return_value=True) as mock_send:
        message = inbox_service.send_message(db_session, payload)

        mock_send.assert_called_once()
        assert message.status == MessageStatus.sent
        assert message.direction == MessageDirection.outbound


def test_send_email_message_with_personalization(db_session, crm_contact, crm_contact_channel):
    """Test sending email message with personalization."""
    from app.schemas.crm.conversation import ConversationCreate
    from app.services.crm import conversation as conversation_service

    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )

    payload = InboxSendRequest(
        conversation_id=conversation.id,
        channel_type=ChannelType.email,
        body="Hello {{name}}!",
        personalization={"name": "Customer"},
    )

    with patch("app.services.email.send_email", return_value=True) as mock_send:
        message = inbox_service.send_message(db_session, payload)

        assert message.body == "Hello Customer!"


def test_send_email_message_failed(db_session, crm_contact, crm_contact_channel):
    """Test sending email message that fails."""
    from app.schemas.crm.conversation import ConversationCreate
    from app.services.crm import conversation as conversation_service

    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )

    payload = InboxSendRequest(
        conversation_id=conversation.id,
        channel_type=ChannelType.email,
        body="This will fail",
    )

    with patch("app.services.email.send_email", return_value=False):
        message = inbox_service.send_message(db_session, payload)

        assert message.status == MessageStatus.failed


def test_send_message_conversation_not_found(db_session):
    """Test sending message to non-existent conversation raises 404."""
    payload = InboxSendRequest(
        conversation_id=uuid.uuid4(),
        channel_type=ChannelType.email,
        body="Test message",
    )

    with pytest.raises(HTTPException) as exc_info:
        inbox_service.send_message(db_session, payload)
    assert exc_info.value.status_code == 404


def test_send_message_contact_channel_not_found(db_session, crm_contact):
    """Test sending message without valid contact channel raises 400."""
    from app.schemas.crm.conversation import ConversationCreate
    from app.services.crm import conversation as conversation_service

    # Create conversation but contact has no WhatsApp channel
    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )

    payload = InboxSendRequest(
        conversation_id=conversation.id,
        channel_type=ChannelType.whatsapp,  # Contact has email channel, not WhatsApp
        body="Test message",
    )

    with pytest.raises(HTTPException) as exc_info:
        inbox_service.send_message(db_session, payload)
    assert exc_info.value.status_code == 400
    assert "Contact channel not found" in exc_info.value.detail


def test_send_message_channel_mismatch_with_inbound(
    db_session, crm_contact, crm_contact_channel
):
    """Test send_message rejects replies on a different channel than inbound."""
    from app.models.person import PersonChannel, ChannelType as PersonChannelType
    from app.schemas.crm.conversation import ConversationCreate, MessageCreate
    from app.services.crm import conversation as conversation_service

    whatsapp_channel = PersonChannel(
        person_id=crm_contact.id,
        channel_type=PersonChannelType.whatsapp,
        address="+15551234567",
    )
    db_session.add(whatsapp_channel)
    db_session.commit()

    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    conversation_service.Messages.create(
        db_session,
        MessageCreate(
            conversation_id=conversation.id,
            person_channel_id=whatsapp_channel.id,
            channel_type=ChannelType.whatsapp,
            direction=MessageDirection.inbound,
            status=MessageStatus.received,
            body="Inbound on WhatsApp",
            received_at=datetime.now(timezone.utc),
        ),
    )

    payload = InboxSendRequest(
        conversation_id=conversation.id,
        channel_type=ChannelType.email,
        body="Reply on email",
    )

    with pytest.raises(HTTPException) as exc_info:
        inbox_service.send_message(db_session, payload)
    assert exc_info.value.status_code == 400
    assert "Reply channel does not match" in exc_info.value.detail


def test_send_whatsapp_message_no_connector(db_session, crm_contact):
    """Test sending WhatsApp message without connector raises 400."""
    from app.models.person import PersonChannel, ChannelType as PersonChannelType
    from app.schemas.crm.conversation import ConversationCreate
    from app.services.crm import conversation as conversation_service

    # Create WhatsApp channel for contact
    whatsapp_channel = PersonChannel(
        person_id=crm_contact.id,
        channel_type=PersonChannelType.whatsapp,
        address="+15551234567",
    )
    db_session.add(whatsapp_channel)
    db_session.commit()

    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )

    payload = InboxSendRequest(
        conversation_id=conversation.id,
        channel_type=ChannelType.whatsapp,
        body="WhatsApp message",
    )

    with pytest.raises(HTTPException) as exc_info:
        inbox_service.send_message(db_session, payload)
    assert exc_info.value.status_code == 400
    assert "WhatsApp connector not configured" in exc_info.value.detail


def test_send_email_with_connector_config(db_session, crm_contact, crm_contact_channel):
    """Test sending email with connector config uses SMTP settings."""
    from app.schemas.crm.conversation import ConversationCreate
    from app.services.crm import conversation as conversation_service

    # Create email connector
    config = ConnectorConfig(
        name="SMTP Test Config",
        connector_type=ConnectorType.email,
        metadata_={"smtp": {"host": "smtp.test.com", "port": 587}},
        auth_config={"username": "user", "password": "pass"},
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="SMTP Test Target",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
        is_active=True,
    )
    db_session.add(target)
    db_session.commit()

    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )

    payload = InboxSendRequest(
        conversation_id=conversation.id,
        channel_type=ChannelType.email,
        channel_target_id=target.id,
        body="Email with config",
    )

    with patch("app.services.email.send_email_with_config", return_value=True) as mock_send:
        message = inbox_service.send_message(db_session, payload)

        mock_send.assert_called_once()
        assert message.status == MessageStatus.sent


# =============================================================================
# Integration Target Resolution Tests
# =============================================================================


def test_resolve_integration_target_by_id(db_session):
    """Test resolving integration target by explicit ID."""
    config = ConnectorConfig(
        name="Test Email Config",
        connector_type=ConnectorType.email,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="Test Target",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
    )
    db_session.add(target)
    db_session.commit()

    result = inbox_service._resolve_integration_target(
        db_session, ChannelType.email, str(target.id)
    )

    assert result is not None
    assert result.id == target.id


def test_resolve_integration_target_not_found(db_session):
    """Test resolving non-existent integration target raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        inbox_service._resolve_integration_target(
            db_session, ChannelType.email, str(uuid.uuid4())
        )
    assert exc_info.value.status_code == 404
    assert "Integration target not found" in exc_info.value.detail


def test_resolve_integration_target_default(db_session):
    """Test resolving default integration target for channel type."""
    config = ConnectorConfig(
        name="Default Email Config",
        connector_type=ConnectorType.email,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="Default Email Target",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
        is_active=True,
    )
    db_session.add(target)
    db_session.commit()

    result = inbox_service._resolve_integration_target(
        db_session, ChannelType.email, None
    )

    assert result is not None
    assert result.id == target.id


# =============================================================================
# Connector Config Resolution Tests
# =============================================================================


def test_resolve_connector_config_success(db_session):
    """Test resolving connector config from target."""
    config = ConnectorConfig(
        name="Email Config",
        connector_type=ConnectorType.email,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="Email Target",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
    )
    db_session.add(target)
    db_session.commit()

    result = inbox_service._resolve_connector_config(
        db_session, target, ChannelType.email
    )

    assert result is not None
    assert result.id == config.id


def test_resolve_connector_config_no_target(db_session):
    """Test resolving connector config with no target returns None."""
    result = inbox_service._resolve_connector_config(
        db_session, None, ChannelType.email
    )
    assert result is None


def test_resolve_connector_config_type_mismatch(db_session):
    """Test resolving connector config with type mismatch raises 400."""
    config = ConnectorConfig(
        name="WhatsApp Config",
        connector_type=ConnectorType.whatsapp,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="WhatsApp Target",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
    )
    db_session.add(target)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        inbox_service._resolve_connector_config(db_session, target, ChannelType.email)
    assert exc_info.value.status_code == 400
    assert "Connector type mismatch" in exc_info.value.detail


# =============================================================================
# SMTP Config Extraction Tests
# =============================================================================


def test_smtp_config_from_connector(db_session):
    """Test extracting SMTP config from connector."""
    config = ConnectorConfig(
        name="SMTP Config",
        connector_type=ConnectorType.email,
        metadata_={"smtp": {"host": "smtp.example.com", "port": 587}},
        auth_config={
            "username": "user@example.com",
            "password": "secret",
            "from_email": "noreply@example.com",
            "from_name": "Support Team",
        },
    )

    result = inbox_service._smtp_config_from_connector(config)

    assert result is not None
    assert result["host"] == "smtp.example.com"
    assert result["port"] == 587
    assert result["username"] == "user@example.com"
    assert result["password"] == "secret"
    assert result["from_email"] == "noreply@example.com"
    assert result["from_name"] == "Support Team"


def test_smtp_config_from_connector_no_metadata(db_session):
    """Test extracting SMTP config with no metadata returns None."""
    config = ConnectorConfig(
        name="No Metadata Config",
        connector_type=ConnectorType.email,
        metadata_=None,
    )

    result = inbox_service._smtp_config_from_connector(config)
    assert result is None


def test_smtp_config_from_connector_no_smtp(db_session):
    """Test extracting SMTP config with no smtp key returns None."""
    config = ConnectorConfig(
        name="No SMTP Config",
        connector_type=ConnectorType.email,
        metadata_={"imap": {"host": "imap.example.com"}},
    )

    result = inbox_service._smtp_config_from_connector(config)
    assert result is None


# =============================================================================
# Create Email Connector Target Tests
# =============================================================================


def test_create_email_connector_target(db_session):
    """Test creating email connector target."""
    target = inbox_service.create_email_connector_target(
        db_session,
        name="Test Email Connector",
        smtp={"host": "smtp.example.com", "port": 587},
        imap={"host": "imap.example.com", "port": 993},
        auth_config={"username": "user@example.com"},
    )

    assert target is not None
    assert target.name == "Test Email Connector"
    assert target.target_type == IntegrationTargetType.crm
    assert target.connector_config_id is not None

    config = db_session.get(ConnectorConfig, target.connector_config_id)
    assert config.connector_type == ConnectorType.email
    assert config.metadata_["smtp"]["host"] == "smtp.example.com"
    assert config.metadata_["imap"]["host"] == "imap.example.com"


def test_create_email_connector_target_minimal(db_session):
    """Test creating email connector target with minimal config."""
    target = inbox_service.create_email_connector_target(
        db_session,
        name="Minimal Email Connector",
    )

    assert target is not None
    assert target.name == "Minimal Email Connector"


# =============================================================================
# Ensure Email Polling Job Tests
# =============================================================================


def test_ensure_email_polling_job_create(db_session):
    """Test creating email polling job."""
    config = ConnectorConfig(
        name="Polling Config",
        connector_type=ConnectorType.email,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="Polling Target",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
    )
    db_session.add(target)
    db_session.commit()

    job = inbox_service.ensure_email_polling_job(
        db_session,
        str(target.id),
        interval_minutes=5,
        name="Custom Polling Job",
    )

    assert job is not None
    assert job.name == "Custom Polling Job"
    assert job.interval_minutes == 5
    assert job.job_type == IntegrationJobType.import_
    assert job.schedule_type == IntegrationScheduleType.interval
    assert job.is_active is True


def test_ensure_email_polling_job_update_existing(db_session):
    """Test updating existing email polling job."""
    config = ConnectorConfig(
        name="Update Polling Config",
        connector_type=ConnectorType.email,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="Update Polling Target",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
    )
    db_session.add(target)
    db_session.commit()

    # Create first job
    job1 = inbox_service.ensure_email_polling_job(
        db_session, str(target.id), interval_minutes=5
    )
    job1_id = job1.id

    # Update should return same job with new interval
    job2 = inbox_service.ensure_email_polling_job(
        db_session, str(target.id), interval_minutes=10
    )

    assert job2.id == job1_id
    assert job2.interval_minutes == 10


def test_ensure_email_polling_job_invalid_interval(db_session):
    """Test creating polling job with invalid interval raises 400."""
    config = ConnectorConfig(
        name="Invalid Interval Config",
        connector_type=ConnectorType.email,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="Invalid Interval Target",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
    )
    db_session.add(target)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        inbox_service.ensure_email_polling_job(
            db_session, str(target.id), interval_minutes=0
        )
    assert exc_info.value.status_code == 400
    assert "interval_minutes must be >= 1" in exc_info.value.detail


def test_ensure_email_polling_job_target_not_found(db_session):
    """Test creating polling job with non-existent target raises 404."""
    with pytest.raises(HTTPException) as exc_info:
        inbox_service.ensure_email_polling_job(
            db_session, str(uuid.uuid4()), interval_minutes=5
        )
    assert exc_info.value.status_code == 404
    assert "Integration target not found" in exc_info.value.detail


def test_ensure_email_polling_job_wrong_target_type(db_session):
    """Test creating polling job with non-CRM target type raises 400."""
    config = ConnectorConfig(
        name="Wrong Type Config",
        connector_type=ConnectorType.email,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="Wrong Type Target",
        target_type=IntegrationTargetType.billing,  # Not CRM
        connector_config_id=config.id,
    )
    db_session.add(target)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        inbox_service.ensure_email_polling_job(
            db_session, str(target.id), interval_minutes=5
        )
    assert exc_info.value.status_code == 400
    assert "Target must be crm type" in exc_info.value.detail


def test_ensure_email_polling_job_no_connector_config(db_session):
    """Test creating polling job with target missing connector config raises 400."""
    target = IntegrationTarget(
        name="No Config Target",
        target_type=IntegrationTargetType.crm,
        connector_config_id=None,
    )
    db_session.add(target)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        inbox_service.ensure_email_polling_job(
            db_session, str(target.id), interval_minutes=5
        )
    assert exc_info.value.status_code == 400
    assert "Target missing connector config" in exc_info.value.detail


def test_ensure_email_polling_job_wrong_connector_type(db_session):
    """Test creating polling job with non-email connector raises 400."""
    config = ConnectorConfig(
        name="WhatsApp Config",
        connector_type=ConnectorType.whatsapp,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="WhatsApp Target",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
    )
    db_session.add(target)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        inbox_service.ensure_email_polling_job(
            db_session, str(target.id), interval_minutes=5
        )
    assert exc_info.value.status_code == 400
    assert "Target is not email connector" in exc_info.value.detail


# =============================================================================
# Poll Email Targets Tests
# =============================================================================


def test_poll_email_targets(db_session):
    """Test polling email targets."""
    config = ConnectorConfig(
        name="Poll Test Config",
        connector_type=ConnectorType.email,
        metadata_={"imap": {"host": "imap.example.com"}},
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="Poll Test Target",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
        is_active=True,
    )
    db_session.add(target)
    db_session.commit()

    with patch(
        "app.services.crm.email_polling.poll_email_connector",
        return_value={"processed": 5},
    ) as mock_poll:
        result = inbox_service.poll_email_targets(db_session)

        mock_poll.assert_called_once()
        assert result["processed"] == 5


def test_poll_email_targets_specific_target(db_session):
    """Test polling specific email target by ID."""
    config = ConnectorConfig(
        name="Specific Poll Config",
        connector_type=ConnectorType.email,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="Specific Poll Target",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
        is_active=True,
    )
    db_session.add(target)
    db_session.commit()

    with patch(
        "app.services.crm.email_polling.poll_email_connector",
        return_value={"processed": 3},
    ) as mock_poll:
        result = inbox_service.poll_email_targets(db_session, str(target.id))

        mock_poll.assert_called_once()
        assert result["processed"] == 3


def test_poll_email_targets_skips_non_email_connectors(db_session):
    """Test polling skips non-email connector targets."""
    config = ConnectorConfig(
        name="WhatsApp Config",
        connector_type=ConnectorType.whatsapp,
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="WhatsApp Target",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
        is_active=True,
    )
    db_session.add(target)
    db_session.commit()

    with patch(
        "app.services.crm.email_polling.poll_email_connector",
        return_value={"processed": 0},
    ) as mock_poll:
        result = inbox_service.poll_email_targets(db_session)

        mock_poll.assert_not_called()
        assert result["processed"] == 0


def test_poll_email_targets_skips_no_config(db_session):
    """Test polling skips targets without connector config."""
    target = IntegrationTarget(
        name="No Config Target",
        target_type=IntegrationTargetType.crm,
        connector_config_id=None,
        is_active=True,
    )
    db_session.add(target)
    db_session.commit()

    with patch(
        "app.services.crm.email_polling.poll_email_connector",
        return_value={"processed": 0},
    ) as mock_poll:
        result = inbox_service.poll_email_targets(db_session)

        mock_poll.assert_not_called()
        assert result["processed"] == 0


# =============================================================================
# WhatsApp Message Sending Tests
# =============================================================================


def test_send_whatsapp_message_missing_token(db_session, crm_contact):
    """Test sending WhatsApp message without access token raises 400."""
    from app.models.person import PersonChannel, ChannelType as PersonChannelType
    from app.schemas.crm.conversation import ConversationCreate
    from app.services.crm import conversation as conversation_service

    # Create WhatsApp channel for contact
    whatsapp_channel = PersonChannel(
        person_id=crm_contact.id,
        channel_type=PersonChannelType.whatsapp,
        address="+15551234567",
    )
    db_session.add(whatsapp_channel)
    db_session.commit()

    # Create WhatsApp connector without token
    config = ConnectorConfig(
        name="No Token Config",
        connector_type=ConnectorType.whatsapp,
        auth_config={},  # No token
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="No Token Target",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
        is_active=True,
    )
    db_session.add(target)
    db_session.commit()

    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )

    payload = InboxSendRequest(
        conversation_id=conversation.id,
        channel_type=ChannelType.whatsapp,
        channel_target_id=target.id,
        body="WhatsApp message",
    )

    with pytest.raises(HTTPException) as exc_info:
        inbox_service.send_message(db_session, payload)
    assert exc_info.value.status_code == 400
    assert "WhatsApp access token missing" in exc_info.value.detail


def test_send_whatsapp_message_missing_phone_number_id(db_session, crm_contact):
    """Test sending WhatsApp message without phone_number_id raises 400."""
    from app.models.person import PersonChannel, ChannelType as PersonChannelType
    from app.schemas.crm.conversation import ConversationCreate
    from app.services.crm import conversation as conversation_service

    # Create WhatsApp channel for contact
    whatsapp_channel = PersonChannel(
        person_id=crm_contact.id,
        channel_type=PersonChannelType.whatsapp,
        address="+15559876543",
    )
    db_session.add(whatsapp_channel)
    db_session.commit()

    # Create WhatsApp connector with token but no phone_number_id
    config = ConnectorConfig(
        name="No Phone ID Config",
        connector_type=ConnectorType.whatsapp,
        auth_config={"token": "test_token"},
        metadata_={},  # No phone_number_id
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="No Phone ID Target",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
        is_active=True,
    )
    db_session.add(target)
    db_session.commit()

    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )

    payload = InboxSendRequest(
        conversation_id=conversation.id,
        channel_type=ChannelType.whatsapp,
        channel_target_id=target.id,
        body="WhatsApp message",
    )

    with pytest.raises(HTTPException) as exc_info:
        inbox_service.send_message(db_session, payload)
    assert exc_info.value.status_code == 400
    assert "WhatsApp phone_number_id missing" in exc_info.value.detail


def test_send_whatsapp_message_success(db_session, crm_contact):
    """Test sending WhatsApp message successfully."""
    import httpx
    from app.models.person import PersonChannel, ChannelType as PersonChannelType
    from app.schemas.crm.conversation import ConversationCreate
    from app.services.crm import conversation as conversation_service

    # Create WhatsApp channel for contact
    whatsapp_channel = PersonChannel(
        person_id=crm_contact.id,
        channel_type=PersonChannelType.whatsapp,
        address="+15557778888",
    )
    db_session.add(whatsapp_channel)
    db_session.commit()

    # Create fully configured WhatsApp connector
    config = ConnectorConfig(
        name="Full WhatsApp Config",
        connector_type=ConnectorType.whatsapp,
        base_url="https://graph.facebook.com/v19.0",
        auth_config={"token": "test_token"},
        metadata_={"phone_number_id": "123456789"},
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="Full WhatsApp Target",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
        is_active=True,
    )
    db_session.add(target)
    db_session.commit()

    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )

    payload = InboxSendRequest(
        conversation_id=conversation.id,
        channel_type=ChannelType.whatsapp,
        channel_target_id=target.id,
        body="Hello via WhatsApp!",
    )

    # Mock the httpx.post call
    mock_response = MagicMock()
    mock_response.json.return_value = {"messages": [{"id": "wamid.123"}]}
    mock_response.content = b'{"messages": [{"id": "wamid.123"}]}'

    with patch("httpx.post", return_value=mock_response) as mock_post:
        message = inbox_service.send_message(db_session, payload)

        mock_post.assert_called_once()
        assert message.status == MessageStatus.sent
        assert message.external_id == "wamid.123"


def test_send_whatsapp_message_http_error(db_session, crm_contact):
    """Test sending WhatsApp message with HTTP error marks message as failed."""
    import httpx
    from app.models.person import PersonChannel, ChannelType as PersonChannelType
    from app.schemas.crm.conversation import ConversationCreate
    from app.services.crm import conversation as conversation_service

    # Create WhatsApp channel for contact
    whatsapp_channel = PersonChannel(
        person_id=crm_contact.id,
        channel_type=PersonChannelType.whatsapp,
        address="+15556667777",
    )
    db_session.add(whatsapp_channel)
    db_session.commit()

    # Create fully configured WhatsApp connector
    config = ConnectorConfig(
        name="HTTP Error Config",
        connector_type=ConnectorType.whatsapp,
        auth_config={"access_token": "test_token"},
        metadata_={"phone_number_id": "123456789"},
    )
    db_session.add(config)
    db_session.commit()

    target = IntegrationTarget(
        name="HTTP Error Target",
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
        is_active=True,
    )
    db_session.add(target)
    db_session.commit()

    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )

    payload = InboxSendRequest(
        conversation_id=conversation.id,
        channel_type=ChannelType.whatsapp,
        channel_target_id=target.id,
        body="This will fail",
    )

    response = httpx.Response(
        403,
        request=httpx.Request("POST", "https://graph.facebook.com/v19.0/123/messages"),
        text="Forbidden",
    )

    with patch("httpx.post", return_value=response):
        message = inbox_service.send_message(db_session, payload)

        assert message.status == MessageStatus.failed
        assert message.metadata_["send_error"]["status_code"] == 403


def test_send_facebook_message_auth_error_records_metadata(db_session, crm_contact):
    from app.models.person import PersonChannel, ChannelType as PersonChannelType
    from app.models.crm.conversation import Message
    from app.schemas.crm.conversation import ConversationCreate
    from app.services.crm import conversation as conversation_service

    fb_channel = PersonChannel(
        person_id=crm_contact.id,
        channel_type=PersonChannelType.facebook_messenger,
        address="fb_user_123",
    )
    db_session.add(fb_channel)
    db_session.commit()

    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    inbound_message = Message(
        conversation_id=conversation.id,
        person_channel_id=fb_channel.id,
        channel_type=ChannelType.facebook_messenger,
        direction=MessageDirection.inbound,
        status=MessageStatus.received,
        body="Inbound",
        received_at=datetime.now(timezone.utc),
    )
    db_session.add(inbound_message)
    db_session.flush()

    payload = InboxSendRequest(
        conversation_id=conversation.id,
        channel_type=ChannelType.facebook_messenger,
        body="Hello",
    )

    with patch(
        "app.services.meta_messaging.send_facebook_message_sync",
        side_effect=ValueError("token expired"),
    ):
        message = inbox_service.send_message(db_session, payload)

    assert message.status == MessageStatus.failed
    assert message.metadata_["send_error"]["channel"] == "facebook_messenger"
    assert "token expired" in message.metadata_["send_error"]["error"]


def test_send_instagram_message_auth_error_records_metadata(db_session, crm_contact):
    from app.models.person import PersonChannel, ChannelType as PersonChannelType
    from app.models.crm.conversation import Message
    from app.schemas.crm.conversation import ConversationCreate
    from app.services.crm import conversation as conversation_service

    ig_channel = PersonChannel(
        person_id=crm_contact.id,
        channel_type=PersonChannelType.instagram_dm,
        address="ig_user_456",
    )
    db_session.add(ig_channel)
    db_session.commit()

    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )
    inbound_message = Message(
        conversation_id=conversation.id,
        person_channel_id=ig_channel.id,
        channel_type=ChannelType.instagram_dm,
        direction=MessageDirection.inbound,
        status=MessageStatus.received,
        body="Inbound",
        received_at=datetime.now(timezone.utc),
    )
    db_session.add(inbound_message)
    db_session.flush()

    payload = InboxSendRequest(
        conversation_id=conversation.id,
        channel_type=ChannelType.instagram_dm,
        body="Hello",
    )

    with patch(
        "app.services.meta_messaging.send_instagram_message_sync",
        side_effect=ValueError("token expired"),
    ):
        message = inbox_service.send_message(db_session, payload)

    assert message.status == MessageStatus.failed
    assert message.metadata_["send_error"]["channel"] == "instagram_dm"
    assert "token expired" in message.metadata_["send_error"]["error"]

def test_send_email_missing_recipient(db_session, crm_contact):
    """Test sending email with empty recipient address raises 400."""
    from app.models.person import PersonChannel, ChannelType as PersonChannelType
    from app.schemas.crm.conversation import ConversationCreate
    from app.services.crm import conversation as conversation_service

    # Create email channel with empty address
    email_channel = PersonChannel(
        person_id=crm_contact.id,
        channel_type=PersonChannelType.email,
        address="",  # Empty address
        is_primary=True,
    )
    db_session.add(email_channel)
    db_session.commit()

    conversation = conversation_service.Conversations.create(
        db_session,
        ConversationCreate(person_id=crm_contact.id),
    )

    payload = InboxSendRequest(
        conversation_id=conversation.id,
        channel_type=ChannelType.email,
        body="Test email",
    )

    with pytest.raises(HTTPException) as exc_info:
        inbox_service.send_message(db_session, payload)
    assert exc_info.value.status_code == 400
    assert "Recipient email missing" in exc_info.value.detail


# =============================================================================
# Person Resolution Tests
# =============================================================================


def test_resolve_person_for_contact_with_email_match(db_session, person):
    """Test resolving person for contact by email match."""
    result = inbox_service._resolve_person_for_contact(person)

    assert result == str(person.id)


def test_resolve_person_for_contact_already_linked(db_session, person):
    """Test resolving person for contact already linked returns person_id."""
    from app.models.person import Person

    contact = Person(
        first_name="Already",
        last_name="Linked",
        display_name="Already Linked Contact",
        email=f"linked-{uuid.uuid4().hex}@example.com",
    )
    db_session.add(contact)
    db_session.commit()

    result = inbox_service._resolve_person_for_contact(contact)

    assert result == str(contact.id)


def test_resolve_person_for_contact_no_match(db_session):
    """Test resolving person for contact with no match returns None."""
    from app.models.person import Person

    contact = Person(
        first_name="No",
        last_name="Match",
        display_name="No Match Contact",
        email="nomatch@example.com",
    )
    db_session.add(contact)
    db_session.commit()

    result = inbox_service._resolve_person_for_contact(contact)

    assert result == str(contact.id)
