"""Compatibility wrapper for inbox inbound helpers."""

from app.services.crm.inbox.inbound import (
    receive_email_message,
    receive_whatsapp_message,
)

__all__ = [
    "receive_email_message",
    "receive_whatsapp_message",
]
