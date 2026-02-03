"""Compatibility wrapper for inbox self-detection helpers."""

from app.services.crm.inbox.self_detection import (
    _extract_self_email_addresses,
    _extract_whatsapp_business_number,
    _is_self_email_message,
    _is_self_whatsapp_message,
    _metadata_indicates_comment,
)

__all__ = [
    "_extract_self_email_addresses",
    "_extract_whatsapp_business_number",
    "_is_self_email_message",
    "_is_self_whatsapp_message",
    "_metadata_indicates_comment",
]
