"""CRM Inbox Service - Public API.

This module provides the public interface for the CRM inbox system.
Implementation is split across multiple submodules for maintainability:

- inbox_normalizers: Address and ID normalization
- inbox_parsing: Email header parsing and conversation token extraction
- inbox_dedup: Message deduplication logic
- inbox_self_detection: Self/agent message detection
- inbox_connectors: Connector/integration target resolution
- inbox_contacts: Contact/person resolution
- inbox_inbound: Inbound message processing (WhatsApp, email webhooks)
- inbox_outbound: Outbound message sending (all channels)
- inbox_polling: Email polling job management
- inbox_queries: Inbox statistics and conversation queries
- inbox_connectors_create: Connector creation utilities
"""
from __future__ import annotations

# Re-export public API functions
from app.services.crm.inbox_inbound import (
    receive_email_message,
    receive_whatsapp_message,
)
from app.services.crm.inbox_outbound import send_message
from app.services.crm.inbox_queries import (
    get_channel_stats,
    get_inbox_stats,
    list_inbox_conversations,
)
from app.services.crm.inbox_polling import (
    ensure_email_polling_job,
    poll_email_targets,
)
from app.services.crm.inbox_connectors_create import (
    create_email_connector_target,
    create_whatsapp_connector_target,
)

# Re-export internal functions used by other modules (e.g., web/admin/crm.py)
from app.services.crm.inbox_connectors import _smtp_config_from_connector

__all__ = [
    # Inbound message processing
    "receive_email_message",
    "receive_whatsapp_message",
    # Outbound message sending
    "send_message",
    # Queries and statistics
    "get_channel_stats",
    "get_inbox_stats",
    "list_inbox_conversations",
    # Polling
    "ensure_email_polling_job",
    "poll_email_targets",
    # Connector creation
    "create_email_connector_target",
    "create_whatsapp_connector_target",
    # Internal (for backwards compatibility)
    "_smtp_config_from_connector",
]
