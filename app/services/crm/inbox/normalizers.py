"""Address and ID normalization utilities for CRM inbox.

This module provides functions to normalize external IDs, email addresses,
phone numbers, and other channel-specific addresses for consistent storage
and lookup in the CRM inbox system.
"""

import hashlib

from app.models.crm.enums import ChannelType


def _normalize_external_id(raw_id: str | None) -> str | None:
    """Normalize an external ID, hashing if over 120 characters.

    Args:
        raw_id: The raw external ID string to normalize.

    Returns:
        The normalized ID, a SHA-256 hash if too long, or None if empty.
    """
    if not raw_id:
        return None
    candidate = raw_id.strip()
    if not candidate:
        return None
    if len(candidate) > 120:
        return hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    return candidate


def _normalize_email_message_id(raw_id: str | None) -> str | None:
    """Clean an email message ID by stripping angle brackets.

    Args:
        raw_id: The raw email message ID (e.g., "<msg-id@example.com>").

    Returns:
        The cleaned message ID without angle brackets, or None if empty.
    """
    if not raw_id:
        return None
    cleaned = raw_id.strip().strip("<>").strip()
    return _normalize_external_id(cleaned)


def _normalize_email_address(address: str | None) -> str | None:
    """Lowercase and strip an email address.

    Args:
        address: The email address to normalize.

    Returns:
        The lowercased, stripped email address, or None if empty.
    """
    if not address:
        return None
    candidate = address.strip().lower()
    return candidate or None


def _normalize_phone_address(value: str | None) -> str | None:
    """Extract digits only from a phone number.

    Args:
        value: The phone number string to normalize.

    Returns:
        A string containing only the digits, or None if no digits found.
    """
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if not digits:
        return None
    return f"+{digits}"


def _normalize_channel_address(channel_type: ChannelType, address: str | None) -> str | None:
    """Dispatch to the appropriate normalizer based on channel type.

    Args:
        channel_type: The type of communication channel.
        address: The address to normalize.

    Returns:
        The normalized address appropriate for the channel type, or None if empty.
    """
    if not address:
        return None
    if channel_type == ChannelType.email:
        return _normalize_email_address(address)
    if channel_type == ChannelType.whatsapp:
        return _normalize_phone_address(address)
    return address.strip()
