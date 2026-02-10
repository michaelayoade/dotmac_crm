"""Inbound message processing for CRM inbox.

Handles receiving messages from webhooks (WhatsApp, email) and routing
 them to the appropriate conversation.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.schemas.crm.inbox import EmailWebhookPayload, WhatsAppWebhookPayload
from app.services.crm.inbox.handlers import EmailHandler, WhatsAppHandler


def receive_whatsapp_message(db: Session, payload: WhatsAppWebhookPayload):
    """Process an inbound WhatsApp message from webhook."""
    return WhatsAppHandler().receive(db, payload)


def receive_email_message(db: Session, payload: EmailWebhookPayload):
    """Process an inbound email message from webhook."""
    return EmailHandler().receive(db, payload)


def receive_sms_message(db: Session, payload):
    # Placeholder for SMS inbound support.
    return None


def receive_chat_message(db: Session, payload):
    # Placeholder for chat inbound support.
    return None
