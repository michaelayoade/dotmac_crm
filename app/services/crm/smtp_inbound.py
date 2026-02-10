"""Compatibility wrapper for CRM SMTP inbound service."""

from app.services.crm.inbox.smtp_inbound import (
    CRMInboundSMTPHandler,
    start_smtp_inbound_server,
    start_smtp_server,
    stop_smtp_inbound_server,
    stop_smtp_server,
)

__all__ = [
    "CRMInboundSMTPHandler",
    "start_smtp_inbound_server",
    "start_smtp_server",
    "stop_smtp_inbound_server",
    "stop_smtp_server",
]
