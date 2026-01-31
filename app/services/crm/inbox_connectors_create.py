"""CRM Inbox Connector Creation Module.

This module provides functions for creating email and WhatsApp connector
targets used by the CRM inbox for omni-channel messaging.
"""

from sqlalchemy.orm import Session

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.integration import IntegrationTarget, IntegrationTargetType


def create_email_connector_target(
    db: Session,
    name: str,
    smtp: dict | None = None,
    imap: dict | None = None,
    pop3: dict | None = None,
    auth_config: dict | None = None,
):
    """Create an email connector and integration target.

    Args:
        db: Database session
        name: Name for the connector and target
        smtp: SMTP server configuration dict
        imap: IMAP server configuration dict
        pop3: POP3 server configuration dict
        auth_config: Authentication credentials

    Returns:
        IntegrationTarget: The created integration target
    """
    config = ConnectorConfig(
        name=name,
        connector_type=ConnectorType.email,
        auth_config=auth_config,
        metadata_={"smtp": smtp, "imap": imap, "pop3": pop3},
    )
    db.add(config)
    db.commit()
    db.refresh(config)

    target = IntegrationTarget(
        name=name,
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return target


def create_whatsapp_connector_target(
    db: Session,
    name: str,
    phone_number_id: str | None = None,
    auth_config: dict | None = None,
    base_url: str | None = None,
):
    """Create a WhatsApp connector and integration target.

    Args:
        db: Database session
        name: Name for the connector and target
        phone_number_id: WhatsApp Business API phone number ID
        auth_config: Authentication credentials (token, etc.)
        base_url: Optional custom API base URL

    Returns:
        IntegrationTarget: The created integration target
    """
    metadata = {}
    if phone_number_id:
        metadata["phone_number_id"] = phone_number_id
    config = ConnectorConfig(
        name=name,
        connector_type=ConnectorType.whatsapp,
        auth_config=auth_config,
        base_url=base_url,
        metadata_=metadata or None,
    )
    db.add(config)
    db.commit()
    db.refresh(config)

    target = IntegrationTarget(
        name=name,
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return target
