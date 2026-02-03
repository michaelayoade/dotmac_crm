"""Compatibility wrapper for contact service imports."""

from app.services.crm.contacts.service import (
    Contacts,
    ContactChannels,
    contacts,
    contact_channels,
    get_contact_context,
    get_contact_conversations_summary,
    get_contact_recent_conversations,
    get_contact_recent_projects,
    get_contact_recent_tasks,
    get_contact_recent_tickets,
    get_contact_resolved_conversations,
    get_contact_social_comments,
    get_contact_tags,
    get_or_create_contact_by_channel,
    get_person_with_relationships,
)

__all__ = [
    "Contacts",
    "ContactChannels",
    "contacts",
    "contact_channels",
    "get_contact_context",
    "get_contact_conversations_summary",
    "get_contact_recent_conversations",
    "get_contact_recent_projects",
    "get_contact_recent_tasks",
    "get_contact_recent_tickets",
    "get_contact_resolved_conversations",
    "get_contact_social_comments",
    "get_contact_tags",
    "get_or_create_contact_by_channel",
    "get_person_with_relationships",
]
