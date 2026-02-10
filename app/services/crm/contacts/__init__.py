"""CRM Contacts submodule.

Handles contact and person channel management for CRM conversations.
"""

from app.services.crm.contacts.service import (
    ContactChannels,
    Contacts,
    contact_channels,
    contacts,
)

__all__ = [
    "ContactChannels",
    "Contacts",
    "contact_channels",
    "contacts",
]
