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
from app.services.crm.inbox.inbound import (
    receive_email_message,
    receive_whatsapp_message,
)
from app.services.crm.inbox.outbound import send_message
from app.services.crm.inbox.queries import (
    get_channel_stats,
    get_inbox_stats,
    list_inbox_conversations,
)
from app.services.crm.inbox.polling import (
    ensure_email_polling_job,
    poll_email_targets,
)
from app.services.crm.inbox.connectors_create import (
    create_email_connector_target,
    create_whatsapp_connector_target,
)

# Re-export internal functions used by other modules (e.g., web/admin/crm.py)
from app.services.crm.inbox.connectors import _smtp_config_from_connector

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


class InboxOperations:
    @staticmethod
    def receive_email_message(db, payload):
        return receive_email_message(db, payload)

    @staticmethod
    def receive_whatsapp_message(db, payload):
        return receive_whatsapp_message(db, payload)

    @staticmethod
    def send_message(db, payload, author_id=None):
        return send_message(db, payload, author_id=author_id)

    @staticmethod
    def get_channel_stats(db):
        return get_channel_stats(db)

    @staticmethod
    def get_inbox_stats(db):
        return get_inbox_stats(db)

    @staticmethod
    def list_inbox_conversations(
        db,
        channel=None,
        status=None,
        search=None,
        assignment=None,
        assigned_person_id=None,
        channel_target_id=None,
        exclude_superseded_resolved=True,
        limit=50,
    ):
        return list_inbox_conversations(
            db=db,
            channel=channel,
            status=status,
            search=search,
            assignment=assignment,
            assigned_person_id=assigned_person_id,
            channel_target_id=channel_target_id,
            exclude_superseded_resolved=exclude_superseded_resolved,
            limit=limit,
        )

    @staticmethod
    def ensure_email_polling_job(
        db,
        target_id,
        interval_seconds=None,
        interval_minutes=None,
        name=None,
    ):
        return ensure_email_polling_job(
            db,
            target_id=target_id,
            interval_seconds=interval_seconds,
            interval_minutes=interval_minutes,
            name=name,
        )

    @staticmethod
    def poll_email_targets(db, target_id=None):
        return poll_email_targets(db, target_id=target_id)

    @staticmethod
    def create_email_connector_target(db, payload):
        return create_email_connector_target(db, payload)

    @staticmethod
    def create_whatsapp_connector_target(db, payload):
        return create_whatsapp_connector_target(db, payload)


# Singleton instance
inbox_operations = InboxOperations()
