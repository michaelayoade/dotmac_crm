"""Inbound handler strategy implementations."""

from app.services.crm.inbox.handlers.email import EmailHandler
from app.services.crm.inbox.handlers.whatsapp import WhatsAppHandler

__all__ = [
    "EmailHandler",
    "WhatsAppHandler",
]
