"""Tests for admin inbox UI service helpers."""

from datetime import UTC, datetime

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, MessageDirection, MessageStatus
from app.models.integration import IntegrationTarget, IntegrationTargetType
from app.services.crm.inbox.admin_ui import _resolve_channel_target_id


def test_resolve_channel_target_prefers_latest_inbound_over_stale_preferred(db_session, crm_contact):
    email_config = ConnectorConfig(
        name="Admin UI Email Target Config",
        connector_type=ConnectorType.email,
        is_active=True,
    )
    whatsapp_config = ConnectorConfig(
        name="Admin UI WhatsApp Target Config",
        connector_type=ConnectorType.whatsapp,
        is_active=True,
    )
    db_session.add_all([email_config, whatsapp_config])
    db_session.commit()

    email_target = IntegrationTarget(
        name="Support Mail",
        target_type=IntegrationTargetType.crm,
        connector_config_id=email_config.id,
        is_active=True,
    )
    whatsapp_target = IntegrationTarget(
        name="Dotmac Fiber HelpDesk",
        target_type=IntegrationTargetType.crm,
        connector_config_id=whatsapp_config.id,
        is_active=True,
    )
    db_session.add_all([email_target, whatsapp_target])
    db_session.commit()

    conversation = Conversation(
        person_id=crm_contact.id,
        metadata_={"preferred_channel_target_id": str(email_target.id)},
    )
    db_session.add(conversation)
    db_session.commit()
    db_session.refresh(conversation)

    inbound = Message(
        conversation_id=conversation.id,
        channel_target_id=whatsapp_target.id,
        channel_type=ChannelType.whatsapp,
        direction=MessageDirection.inbound,
        status=MessageStatus.received,
        body="Hello",
        received_at=datetime(2026, 6, 23, 7, 32, tzinfo=UTC),
    )
    db_session.add(inbound)
    db_session.commit()
    db_session.refresh(inbound)

    assert _resolve_channel_target_id(db_session, conversation) == str(whatsapp_target.id)
