"""Compatibility wrapper for inbox email polling imports."""

from app.services.crm.inbox.email_polling import poll_email_connector

__all__ = ["poll_email_connector"]
