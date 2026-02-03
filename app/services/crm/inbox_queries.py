"""Compatibility wrapper for inbox query helpers."""

from app.services.crm.inbox.queries import (
    get_channel_stats,
    get_inbox_stats,
    list_inbox_conversations,
)

__all__ = [
    "get_channel_stats",
    "get_inbox_stats",
    "list_inbox_conversations",
]
