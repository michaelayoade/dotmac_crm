"""Compatibility wrapper for conversation service imports."""

from app.services.crm.conversations.service import (
    ConversationAssignments,
    ConversationTags,
    Conversations,
    Messages,
    assign_conversation,
    get_latest_message,
    get_unread_count,
    get_reply_channel_type,
    mark_conversation_read,
    resolve_conversation_contact,
    resolve_open_conversation,
    resolve_open_conversation_for_channel,
    resolve_person_channel,
    unassign_conversation,
)

__all__ = [
    "ConversationAssignments",
    "ConversationTags",
    "Conversations",
    "Messages",
    "assign_conversation",
    "get_latest_message",
    "get_unread_count",
    "get_reply_channel_type",
    "mark_conversation_read",
    "resolve_conversation_contact",
    "resolve_open_conversation",
    "resolve_open_conversation_for_channel",
    "resolve_person_channel",
    "unassign_conversation",
]
