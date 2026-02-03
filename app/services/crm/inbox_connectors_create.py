"""Compatibility wrapper for inbox connector creation helpers."""

from app.services.crm.inbox.connectors_create import (
    create_email_connector_target,
    create_whatsapp_connector_target,
)

__all__ = [
    "create_email_connector_target",
    "create_whatsapp_connector_target",
]
