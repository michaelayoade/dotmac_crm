"""Compatibility wrapper for inbox polling helpers."""

from app.services.crm.inbox.polling import (
    ensure_email_polling_job,
    poll_email_targets,
)

__all__ = [
    "ensure_email_polling_job",
    "poll_email_targets",
]
