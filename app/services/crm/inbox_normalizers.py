"""Compatibility wrapper for inbox normalizers."""

from app.services.crm.inbox.normalizers import (
    _normalize_channel_address,
    _normalize_email_address,
    _normalize_email_message_id,
    _normalize_external_id,
    _normalize_phone_address,
)

__all__ = [
    "_normalize_channel_address",
    "_normalize_email_address",
    "_normalize_email_message_id",
    "_normalize_external_id",
    "_normalize_phone_address",
]
