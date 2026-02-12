"""
Connector and integration target resolution for CRM inbox.

This module handles resolving integration targets and connector configurations
for various channel types (WhatsApp, Email, Facebook Messenger, Instagram DM).
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.crm.enums import ChannelType
from app.models.domain_settings import SettingDomain
from app.models.integration import IntegrationTarget, IntegrationTargetType
from app.services.common import coerce_uuid
from app.services.settings_spec import resolve_value

_DEFAULT_WHATSAPP_TIMEOUT = 10


def _get_whatsapp_api_timeout(db: Session) -> int:
    """
    Get the WhatsApp API timeout from settings.

    Retrieves the configured timeout value for WhatsApp API calls from the
    domain settings. Falls back to the default timeout if the setting is
    not configured or has an invalid value.

    Args:
        db: Database session for querying settings.

    Returns:
        The timeout in seconds for WhatsApp API calls.
    """
    timeout = resolve_value(db, SettingDomain.comms, "whatsapp_api_timeout_seconds")
    if isinstance(timeout, int):
        return timeout
    if isinstance(timeout, str) and timeout.isdigit():
        return int(timeout)
    return _DEFAULT_WHATSAPP_TIMEOUT


def _resolve_integration_target(
    db: Session,
    channel_type: ChannelType,
    target_id: str | None,
) -> IntegrationTarget | None:
    """
    Resolve an integration target for the given channel type.

    If a specific target_id is provided, looks up that target directly.
    Otherwise, finds the most recently created active integration target
    that matches the channel type's connector type.

    Args:
        db: Database session for querying integration targets.
        channel_type: The channel type to find a target for.
        target_id: Optional specific target ID to look up.

    Returns:
        The resolved IntegrationTarget, or None if no matching target exists.

    Raises:
        HTTPException: If a specific target_id is provided but not found (404).
    """
    if target_id:
        target = db.get(IntegrationTarget, coerce_uuid(target_id))
        if not target:
            raise HTTPException(status_code=404, detail="Integration target not found")
        return target

    # Map channel types to connector types
    connector_type_map = {
        ChannelType.whatsapp: ConnectorType.whatsapp,
        ChannelType.email: ConnectorType.email,
        ChannelType.facebook_messenger: ConnectorType.facebook,
        ChannelType.instagram_dm: ConnectorType.facebook,  # Instagram uses same connector as Facebook
    }
    connector_type = connector_type_map.get(channel_type, ConnectorType.email)

    return (
        db.query(IntegrationTarget)
        .filter(IntegrationTarget.target_type == IntegrationTargetType.crm)
        .filter(IntegrationTarget.is_active.is_(True))
        .join(ConnectorConfig, ConnectorConfig.id == IntegrationTarget.connector_config_id)
        .filter(ConnectorConfig.connector_type == connector_type)
        .order_by(IntegrationTarget.created_at.desc())
        .first()
    )


def _resolve_connector_config(
    db: Session,
    target: IntegrationTarget | None,
    channel_type: ChannelType,
) -> ConnectorConfig | None:
    """
    Resolve the connector configuration for an integration target.

    Validates that the connector type matches the expected type for the
    given channel type.

    Args:
        db: Database session for querying connector configs.
        target: The integration target to get the config for.
        channel_type: The channel type to validate against.

    Returns:
        The ConnectorConfig for the target, or None if target is None
        or has no connector config.

    Raises:
        HTTPException: If the connector type doesn't match the expected
            type for the channel (400).
    """
    if not target or not target.connector_config_id:
        return None
    config = db.get(ConnectorConfig, target.connector_config_id)
    if not config:
        return None

    # Map channel types to expected connector types
    expected_map = {
        ChannelType.whatsapp: ConnectorType.whatsapp,
        ChannelType.email: ConnectorType.email,
        ChannelType.facebook_messenger: ConnectorType.facebook,
        ChannelType.instagram_dm: ConnectorType.facebook,
    }
    expected = expected_map.get(channel_type, ConnectorType.email)
    if config.connector_type != expected:
        raise HTTPException(status_code=400, detail="Connector type mismatch")
    return config


def _smtp_config_from_connector(config: ConnectorConfig) -> dict | None:
    """
    Extract SMTP configuration from a connector config.

    Combines SMTP settings from the connector's metadata with authentication
    credentials from the auth_config.

    Args:
        config: The connector configuration to extract SMTP settings from.

    Returns:
        A dictionary containing SMTP configuration with the following
        possible keys:
        - All keys from config.metadata_["smtp"]
        - username: SMTP authentication username
        - password: SMTP authentication password
        - from_email: Sender email address
        - from_name: Sender display name

        Returns None if the config has no metadata or no SMTP configuration.
    """
    if not config.metadata_:
        return None
    smtp = config.metadata_.get("smtp") if isinstance(config.metadata_, dict) else None
    if not smtp:
        return None
    smtp_config = dict(smtp)
    auth_config = config.auth_config or {}
    if auth_config.get("username"):
        smtp_config["username"] = auth_config.get("username")
    if auth_config.get("password"):
        smtp_config["password"] = auth_config.get("password")
    if auth_config.get("from_email"):
        smtp_config["from_email"] = auth_config.get("from_email")
    if auth_config.get("from_name"):
        smtp_config["from_name"] = auth_config.get("from_name")
    # Ensure From domain aligns with authenticated SMTP user when possible.
    username = smtp_config.get("username")
    from_email = smtp_config.get("from_email")
    if isinstance(username, str) and "@" in username:
        username_domain = username.split("@", 1)[1].lower()
        if isinstance(from_email, str) and "@" in from_email:
            from_domain = from_email.split("@", 1)[1].lower()
            if from_domain != username_domain:
                smtp_config["from_email"] = username
        elif not from_email:
            smtp_config["from_email"] = username
    return smtp_config
