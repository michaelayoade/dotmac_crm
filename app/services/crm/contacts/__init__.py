"""CRM Contacts submodule.

Handles contact and person channel management for CRM conversations.
"""

from app.services.crm.contacts.service import (
    Contacts,
    ContactChannels,
    contacts,
    contact_channels,
)

__all__ = [
    "Contacts",
    "ContactChannels",
    "contacts",
    "contact_channels",
]
