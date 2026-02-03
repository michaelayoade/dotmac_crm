"""Compatibility wrapper for inbox contact helpers."""

from app.services.crm.inbox.contacts import (
    _resolve_person_for_contact,
    _resolve_person_for_inbound,
)

__all__ = [
    "_resolve_person_for_contact",
    "_resolve_person_for_inbound",
]
