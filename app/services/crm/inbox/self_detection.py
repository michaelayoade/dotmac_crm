"""
Self/agent message detection for CRM inbox.

This module handles detecting self/agent-sent messages to avoid creating
duplicate inbound messages when processing webhook payloads from various
messaging channels (email, WhatsApp, etc.).
"""

from app.models.connector import ConnectorConfig
from app.models.crm.enums import ChannelType
from app.schemas.crm.inbox import EmailWebhookPayload, WhatsAppWebhookPayload
from app.services.crm.inbox_normalizers import _normalize_email_address, _normalize_phone_address


def _extract_self_email_addresses(config: ConnectorConfig | None) -> set[str]:
    """
    Extract all email addresses that represent "self" from connector configuration.

    Collects email addresses from various configuration locations including
    auth_config, metadata, and SMTP settings that indicate messages sent by
    the business/agent rather than the customer.

    Args:
        config: The connector configuration containing auth and metadata settings.

    Returns:
        A set of normalized email addresses representing the business/agent.
    """
    addresses: set[str] = set()
    if not config:
        return addresses
    auth_config: dict[str, object] = config.auth_config if isinstance(config.auth_config, dict) else {}
    metadata: dict[str, object] = config.metadata_ if isinstance(config.metadata_, dict) else {}
    smtp_value = metadata.get("smtp")
    smtp_config: dict[str, object] = smtp_value if isinstance(smtp_value, dict) else {}

    for value in (
        auth_config.get("username"),
        auth_config.get("from_email"),
        auth_config.get("email"),
        metadata.get("from_email"),
        smtp_config.get("username"),
        smtp_config.get("from_email"),
        smtp_config.get("from"),
    ):
        normalized = _normalize_email_address(value) if isinstance(value, str) else None
        if normalized:
            addresses.add(normalized)
    return addresses


def _metadata_indicates_self(metadata: dict | None) -> bool:
    """
    Check if message metadata indicates the message was sent by the business/agent.

    Examines various metadata flags and fields that different messaging platforms
    use to indicate outbound/self-sent messages.

    Args:
        metadata: The message metadata dictionary from the webhook payload.

    Returns:
        True if metadata indicates the message is from the business/agent,
        False otherwise.
    """
    if not isinstance(metadata, dict):
        return False
    if metadata.get("is_echo") or metadata.get("from_me") or metadata.get("sent_by_business"):
        return True
    sender_type = metadata.get("sender_type") or metadata.get("author_type")
    if isinstance(sender_type, str) and sender_type.lower() in {
        "business",
        "agent",
        "system",
        "page",
        "company",
    }:
        return True
    direction = metadata.get("direction")
    return bool(isinstance(direction, str) and direction.lower() in {"outbound", "sent", "business"})


def _metadata_indicates_comment(metadata: dict | None) -> bool:
    """
    Check if message metadata indicates the message is a comment.

    Comments (such as social media post comments) may need different handling
    than direct messages.

    Args:
        metadata: The message metadata dictionary from the webhook payload.

    Returns:
        True if metadata indicates the message is a comment, False otherwise.
    """
    if not isinstance(metadata, dict):
        return False
    if metadata.get("comment") or metadata.get("comment_id"):
        return True
    source = metadata.get("source")
    if isinstance(source, str) and source.lower() == "comment":
        return True
    msg_type = metadata.get("type")
    return bool(isinstance(msg_type, str) and msg_type.lower() == "comment")


def _extract_whatsapp_business_number(
    metadata: dict | None,
    config: ConnectorConfig | None,
) -> str | None:
    """
    Extract the WhatsApp business phone number from metadata or configuration.

    Checks multiple possible locations where the business phone number might
    be stored in metadata or connector configuration.

    Args:
        metadata: The message metadata dictionary from the webhook payload.
        config: The connector configuration.

    Returns:
        The business phone number as a string, or None if not found.
    """
    if isinstance(metadata, dict):
        for key in (
            "display_phone_number",
            "business_number",
            "from_number",
            "phone_number",
        ):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value
    if not config:
        return None
    config_metadata = config.metadata_ if isinstance(config.metadata_, dict) else {}
    for key in ("display_phone_number", "business_number", "from_number", "phone_number"):
        value = config_metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value
    auth_config = config.auth_config if isinstance(config.auth_config, dict) else {}
    for key in ("display_phone_number", "business_number", "from_number", "phone_number"):
        value = auth_config.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _is_self_email_message(
    payload: EmailWebhookPayload,
    config: ConnectorConfig | None,
) -> bool:
    """
    Determine if an email message was sent by the business/agent.

    Checks both metadata flags and sender address against configured
    business email addresses.

    Args:
        payload: The email webhook payload containing message details.
        config: The connector configuration with email settings.

    Returns:
        True if the email appears to be from the business/agent,
        False if it appears to be from a customer.
    """
    if _metadata_indicates_self(payload.metadata):
        return True
    sender = _normalize_email_address(payload.contact_address)
    if not sender:
        return False
    self_addresses = _extract_self_email_addresses(config)
    if not self_addresses:
        return False
    return sender in self_addresses


def _is_self_whatsapp_message(
    payload: WhatsAppWebhookPayload,
    config: ConnectorConfig | None,
) -> bool:
    """
    Determine if a WhatsApp message was sent by the business.

    Checks both metadata flags and whether the sender number matches
    the configured business WhatsApp number.

    Args:
        payload: The WhatsApp webhook payload containing message details.
        config: The connector configuration with WhatsApp settings.

    Returns:
        True if the message appears to be from the business,
        False if it appears to be from a customer.
    """
    if _metadata_indicates_self(payload.metadata):
        return True
    business_number = _extract_whatsapp_business_number(payload.metadata, config)
    if not business_number:
        return False
    sender = _normalize_phone_address(payload.contact_address)
    owner = _normalize_phone_address(business_number)
    if not sender or not owner:
        return False
    return sender == owner


class SelfDetectionService:
    """Unified self-message detection across channels."""

    def is_self_message(
        self,
        *,
        channel_type: ChannelType,
        sender_address: str | None,
        metadata: dict | None,
        config: ConnectorConfig | None,
    ) -> bool:
        if _metadata_indicates_self(metadata):
            return True
        if channel_type == ChannelType.email:
            return self._is_self_email(sender_address, config)
        if channel_type == ChannelType.whatsapp:
            return self._is_self_whatsapp(sender_address, metadata, config)
        return False

    def _is_self_email(
        self,
        sender_address: str | None,
        config: ConnectorConfig | None,
    ) -> bool:
        sender = _normalize_email_address(sender_address)
        if not sender:
            return False
        self_addresses = _extract_self_email_addresses(config)
        if not self_addresses:
            return False
        return sender in self_addresses

    def _is_self_whatsapp(
        self,
        sender_address: str | None,
        metadata: dict | None,
        config: ConnectorConfig | None,
    ) -> bool:
        business_number = _extract_whatsapp_business_number(metadata, config)
        if not business_number:
            return False
        sender = _normalize_phone_address(sender_address)
        owner = _normalize_phone_address(business_number)
        if not sender or not owner:
            return False
        return sender == owner
