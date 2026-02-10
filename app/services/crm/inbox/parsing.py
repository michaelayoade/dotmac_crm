"""
Email header parsing and conversation token extraction utilities.

This module provides functions for extracting conversation references from
email metadata, including In-Reply-To/References headers and embedded
conversation/ticket tokens in subject lines and addresses.
"""

import re
import uuid
from email.utils import getaddresses

from sqlalchemy import String, cast, func
from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType
from app.services.crm.inbox_normalizers import (
    _normalize_email_message_id,
    _normalize_external_id,
)


def _get_metadata_value(metadata: dict | None, key: str):
    """
    Case-insensitive metadata lookup with headers subdict fallback.

    Searches for a key in the metadata dictionary, first checking exact match,
    then case-insensitive match, and finally checking the nested 'headers'
    dictionary if present.

    Args:
        metadata: Dictionary containing email metadata, may include a 'headers' subdict.
        key: The key to look up (case-insensitive).

    Returns:
        The value associated with the key, or None if not found.
    """
    if not metadata:
        return None
    if key in metadata:
        return metadata[key]
    lower_key = key.lower()
    for meta_key, value in metadata.items():
        if isinstance(meta_key, str) and meta_key.lower() == lower_key:
            return value
    headers = metadata.get("headers") if isinstance(metadata.get("headers"), dict) else None
    if headers:
        for header_key, value in headers.items():
            if isinstance(header_key, str) and header_key.lower() == lower_key:
                return value
    return None


def _extract_message_ids(value) -> list[str]:
    """
    Extract message IDs from In-Reply-To/References header values.

    Parses email header values that may contain one or more message IDs,
    handling both angle-bracket format (<message-id>) and bare IDs.
    Supports list, tuple, set, or string input.

    Args:
        value: Header value(s) containing message IDs. Can be a string,
               list, tuple, or set of strings.

    Returns:
        List of normalized message ID strings, with duplicates removed.
    """
    if not value:
        return []
    candidates: list[str] = []
    if isinstance(value, list | tuple | set):
        for item in value:
            if item:
                candidates.append(str(item))
    else:
        candidates.append(str(value))
    message_ids: list[str] = []
    for raw in candidates:
        for token in re.findall(r"<[^>]+>|[^\s]+", raw):
            stripped = _normalize_email_message_id(token)
            if stripped:
                message_ids.append(stripped)
            raw_norm = _normalize_external_id(token.strip())
            if raw_norm and raw_norm not in message_ids:
                message_ids.append(raw_norm)
    return message_ids


def _extract_conversation_tokens(text: str | None) -> list[str]:
    """
    Find conversation/ticket tokens embedded in text.

    Searches for patterns like 'conv_<uuid>', 'conversation-<uuid>',
    or 'ticket #<uuid>' in the given text. These tokens are commonly
    embedded in email subject lines or reply-to addresses.

    Args:
        text: Text to search for tokens (e.g., email subject line).

    Returns:
        List of extracted token strings (UUID portions only).
    """
    if not text:
        return []
    tokens = []
    conv_matches = re.findall(r"(?:conv[_-]?|conversation[_-]?)([0-9a-fA-F-]{8,36})", text)
    ticket_matches = re.findall(r"(?:ticket\s*#\s*)([0-9a-fA-F-]{8,36})", text, flags=re.IGNORECASE)
    tokens.extend(conv_matches)
    tokens.extend(ticket_matches)
    return tokens


def _find_conversation_by_token(db: Session, token: str) -> Conversation | None:
    """
    Look up a conversation by token or ID prefix.

    Attempts to find a conversation using various matching strategies:
    - Full UUID (32 hex chars or 36 with dashes)
    - Numeric ID in subject (4+ digits, checked before hex to avoid confusion)
    - UUID prefix match (8+ hex chars containing a-f)

    Args:
        db: Database session.
        token: Token string to search for (UUID, prefix, or numeric ID).

    Returns:
        Matching Conversation object, or None if not found.
    """
    cleaned = token.strip().strip("[]()")
    if not cleaned:
        return None
    lowered = cleaned.lower()

    # 1. Full UUID match (32 hex chars or 36 with dashes)
    try:
        if len(lowered) == 32:
            return db.get(Conversation, uuid.UUID(hex=lowered))
        if len(lowered) == 36:
            return db.get(Conversation, uuid.UUID(lowered))
    except (ValueError, AttributeError):
        return None

    # 2. Purely numeric token -> search in subject (must check BEFORE hex pattern)
    # This handles ticket numbers like "98765432" that would otherwise match hex
    if re.fullmatch(r"[0-9]+", lowered) and len(lowered) >= 4:
        return (
            db.query(Conversation)
            .filter(Conversation.subject.ilike(f"%{lowered}%"))
            .order_by(Conversation.updated_at.desc())
            .first()
        )

    # 3. Hex prefix match (must contain at least one a-f to distinguish from numeric)
    # Use replace() to strip dashes from UUID string for prefix matching
    if len(lowered) >= 8 and len(lowered) < 32 and re.fullmatch(r"[0-9a-f]+", lowered):
        return (
            db.query(Conversation)
            .filter(func.replace(func.lower(cast(Conversation.id, String)), "-", "").like(f"{lowered}%"))
            .order_by(Conversation.created_at.desc())
            .first()
        )

    return None


def _resolve_conversation_from_email_metadata(
    db: Session,
    subject: str | None,
    metadata: dict | None,
) -> Conversation | None:
    """
    Resolve a conversation from email headers and metadata.

    Attempts to find an existing conversation by:
    1. Extracting conversation tokens from subject and address fields
    2. Looking up conversations by those tokens
    3. Matching In-Reply-To/References message IDs to existing messages

    Args:
        db: Database session.
        subject: Email subject line.
        metadata: Email metadata dictionary containing headers like
                  reply_to, to, cc, in_reply_to, references.

    Returns:
        Matching Conversation object, or None if no match found.
    """
    address_fields = []
    for key in ("reply_to", "to", "cc"):
        value = _get_metadata_value(metadata, key)
        if value:
            address_fields.append(value)
    addresses = []
    for raw in address_fields:
        if isinstance(raw, list | tuple | set):
            raw_values = [str(item) for item in raw if item]
        else:
            raw_values = [str(raw)]
        for _, addr in getaddresses(raw_values):
            if addr:
                addresses.append(addr)
    tokens = _extract_conversation_tokens(subject)
    for address in addresses:
        tokens.extend(_extract_conversation_tokens(address))
    for token in tokens:
        conv = _find_conversation_by_token(db, token)
        if conv:
            return conv
    in_reply_to = _get_metadata_value(metadata, "in_reply_to")
    references = _get_metadata_value(metadata, "references")
    for msg_id in _extract_message_ids(in_reply_to) + _extract_message_ids(references):
        match = (
            db.query(Message)
            .filter(Message.channel_type == ChannelType.email)
            .filter(Message.external_id == msg_id)
            .order_by(Message.created_at.desc())
            .first()
        )
        if match:
            return match.conversation
    return None
