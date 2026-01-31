"""
Message deduplication logic for inbound CRM messages.

This module provides functions to detect and prevent duplicate message ingestion
across various channels (email, SMS, WhatsApp, etc.). Deduplication is critical
for omni-channel messaging where providers may deliver the same message multiple
times or where polling-based ingestion can encounter the same message repeatedly.
"""

import hashlib
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.crm.conversation import Message
from app.models.crm.enums import ChannelType, MessageDirection


def _build_inbound_dedupe_id(
    channel_type: ChannelType,
    contact_address: str,
    subject: str | None,
    body: str | None,
    received_at: datetime | None,
    source_id: str | None = None,
) -> str:
    """
    Build a hash-based deduplication ID for an inbound message.

    Creates a deterministic SHA-256 hash from message attributes that can be used
    to identify duplicate messages. The hash incorporates channel type, sender
    address, subject, body, and timestamp to create a unique fingerprint.

    Args:
        channel_type: The channel through which the message was received.
        contact_address: The sender's address (email, phone number, etc.).
        subject: The message subject line (may be None for non-email channels).
        body: The message body content.
        received_at: When the message was received (microseconds are truncated).
        source_id: Optional external message ID from the provider.

    Returns:
        A 64-character hexadecimal SHA-256 hash string.

    Note:
        - Email addresses are lowercased for case-insensitive matching.
        - Timestamps have microseconds removed for consistency.
        - All components are joined with pipe characters before hashing.
    """
    address = contact_address.strip()
    if channel_type == ChannelType.email:
        address = address.lower()
    received_at_str = ""
    if received_at:
        received_at_str = received_at.replace(microsecond=0).isoformat()
    key = "|".join(
        [
            channel_type.value,
            source_id or "",
            address,
            subject or "",
            body or "",
            received_at_str,
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _find_duplicate_inbound_message(
    db: Session,
    channel_type: ChannelType,
    person_channel_id,
    channel_target_id,
    message_id: str | None,
    subject: str | None,
    body: str,
    received_at: datetime,
    dedupe_across_targets: bool = False,
) -> Message | None:
    """
    Find an existing duplicate inbound message in the database.

    Uses a two-tier deduplication strategy:
    1. If message_id is provided, look for exact external_id match (preferred).
    2. If no message_id, fall back to content-based matching within a time window.

    Args:
        db: SQLAlchemy database session.
        channel_type: The channel type to filter by.
        person_channel_id: The person channel ID to match for fallback dedup.
        channel_target_id: The channel target (inbox/number) that received the message.
        message_id: External message ID from the provider (e.g., Message-ID header).
        subject: Message subject for fallback matching.
        body: Message body for fallback matching.
        received_at: Message timestamp for time-window fallback matching.
        dedupe_across_targets: If True, ignore channel_target_id when matching
            by message_id (useful for shared inboxes or forwarded messages).

    Returns:
        The existing Message if a duplicate is found, None otherwise.

    Note:
        The fallback time window is +/- 5 minutes to account for clock drift
        and processing delays while avoiding false positives from similar
        messages sent at different times.
    """
    if message_id:
        existing_query = (
            db.query(Message)
            .filter(Message.channel_type == channel_type)
            .filter(Message.external_id == message_id)
        )
        if not dedupe_across_targets:
            if channel_target_id:
                existing_query = existing_query.filter(Message.channel_target_id == channel_target_id)
            else:
                existing_query = existing_query.filter(Message.channel_target_id.is_(None))
        return existing_query.first()

    # Fallback dedupe when providers omit message_id; keep a tight time window to avoid collisions.
    time_window_start = received_at - timedelta(minutes=5)
    time_window_end = received_at + timedelta(minutes=5)
    existing_query = (
        db.query(Message)
        .filter(Message.channel_type == channel_type)
        .filter(Message.direction == MessageDirection.inbound)
        .filter(Message.person_channel_id == person_channel_id)
        .filter(Message.body == body)
        .filter(Message.received_at >= time_window_start)
        .filter(Message.received_at <= time_window_end)
    )
    if subject:
        existing_query = existing_query.filter(Message.subject == subject)
    if channel_target_id:
        existing_query = existing_query.filter(Message.channel_target_id == channel_target_id)
    else:
        existing_query = existing_query.filter(Message.channel_target_id.is_(None))
    return existing_query.first()
