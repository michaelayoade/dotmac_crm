"""CRM Inbox Connector Creation Module.

This module provides functions for creating email and WhatsApp connector
targets used by the CRM inbox for omni-channel messaging.
"""

from sqlalchemy.orm import Session

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.integration import IntegrationTarget, IntegrationTargetType


def _ensure_unique_connector_name(
    db: Session,
    name: str,
    connector_type: ConnectorType,
) -> str:
    """Ensure a unique connector name, reusing if same type exists."""
    existing = (
        db.query(ConnectorConfig)
        .filter(ConnectorConfig.name == name)
        .first()
    )
    if not existing:
        return name
    if existing.connector_type == connector_type:
        return name
    base = f"{name} ({connector_type.value.title()})"
    candidate = base
    idx = 2
    while (
        db.query(ConnectorConfig)
        .filter(ConnectorConfig.name == candidate)
        .first()
        is not None
    ):
        candidate = f"{base} {idx}"
        idx += 1
    return candidate


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
    name = _ensure_unique_connector_name(db, name, ConnectorType.email)
    config = (
        db.query(ConnectorConfig)
        .filter(ConnectorConfig.name == name)
        .filter(ConnectorConfig.connector_type == ConnectorType.email)
        .first()
    )
    if not config:
        config = ConnectorConfig(
            name=name,
            connector_type=ConnectorType.email,
            auth_config=auth_config,
            metadata_={"smtp": smtp, "imap": imap, "pop3": pop3},
        )
        db.add(config)
        db.commit()
        db.refresh(config)
    else:
        metadata = dict(config.metadata_ or {})
        if smtp is not None:
            metadata["smtp"] = smtp
        if imap is not None:
            metadata["imap"] = imap
        if pop3 is not None:
            metadata["pop3"] = pop3
        if metadata:
            config.metadata_ = metadata
        if auth_config is not None:
            config.auth_config = auth_config
        config.is_active = True
        db.commit()
        db.refresh(config)

    target = (
        db.query(IntegrationTarget)
        .filter(IntegrationTarget.connector_config_id == config.id)
        .filter(IntegrationTarget.target_type == IntegrationTargetType.crm)
        .first()
    )
    if not target:
        target = IntegrationTarget(
            name=name,
            target_type=IntegrationTargetType.crm,
            connector_config_id=config.id,
        )
        db.add(target)
        db.commit()
        db.refresh(target)
    else:
        if not target.is_active:
            target.is_active = True
            db.commit()
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
    name = _ensure_unique_connector_name(db, name, ConnectorType.whatsapp)
    metadata = {}
    if phone_number_id:
        metadata["phone_number_id"] = phone_number_id
    config = (
        db.query(ConnectorConfig)
        .filter(ConnectorConfig.name == name)
        .filter(ConnectorConfig.connector_type == ConnectorType.whatsapp)
        .first()
    )
    if not config:
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
    else:
        merged = dict(config.metadata_ or {})
        if phone_number_id:
            merged["phone_number_id"] = phone_number_id
        if merged:
            config.metadata_ = merged
        if auth_config is not None:
            config.auth_config = auth_config
        if base_url is not None:
            config.base_url = base_url
        config.is_active = True
        db.commit()
        db.refresh(config)

    target = (
        db.query(IntegrationTarget)
        .filter(IntegrationTarget.connector_config_id == config.id)
        .filter(IntegrationTarget.target_type == IntegrationTargetType.crm)
        .first()
    )
    if not target:
        target = IntegrationTarget(
            name=name,
            target_type=IntegrationTargetType.crm,
            connector_config_id=config.id,
        )
        db.add(target)
        db.commit()
        db.refresh(target)
    else:
        if not target.is_active:
            target.is_active = True
            db.commit()
    return target
