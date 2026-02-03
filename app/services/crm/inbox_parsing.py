"""Compatibility wrapper for inbox parsing utilities."""

from app.services.crm.inbox.parsing import (
    _extract_conversation_tokens,
    _extract_message_ids,
    _get_metadata_value,
    _resolve_conversation_from_email_metadata,
)

__all__ = [
    "_extract_conversation_tokens",
    "_extract_message_ids",
    "_get_metadata_value",
    "_resolve_conversation_from_email_metadata",
]
