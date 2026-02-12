"""CRM Inbox submodule.

Handles email/message ingestion, processing, and outbound messaging.

Submodules:
- orchestrator: Main inbox operations
- inbound: Message receiving and processing
- outbound: Message sending
- connectors: Channel connector management
- polling: Background email polling
- smtp_inbound: SMTP server for receiving emails
- queries: Inbox search and filtering
- dedup: Duplicate message detection
- parsing: Email parsing utilities
- normalizers: Data normalization
- contacts: Contact resolution from messages
- self_detection: Self-reply detection
"""

# Core inbox operations
from app.services.crm.inbox._core import (
    _render_personalization,
    _resolve_connector_config,
    _resolve_integration_target,
)
from app.services.crm.inbox.connectors import _smtp_config_from_connector

# Connector creation and SMTP config
from app.services.crm.inbox.connectors_create import (
    create_email_connector_target,
    create_whatsapp_connector_target,
)
from app.services.crm.inbox.contacts import _resolve_person_for_contact

# Email polling
from app.services.crm.inbox.email_polling import EmailPoller, poll_email_inbox

# Inbound message processing
from app.services.crm.inbox.inbound import (
    receive_chat_message,
    receive_email_message,
    receive_sms_message,
    receive_whatsapp_message,
)
from app.services.crm.inbox.orchestrator import InboxOperations, inbox_operations

# Outbound messaging
from app.services.crm.inbox.outbound import (
    send_message,
    send_message_with_retry,
    send_outbound_message,
    send_reply,
)
from app.services.crm.inbox.outbox import enqueue_outbound_message
from app.services.crm.inbox.polling import (
    ensure_email_polling_job,
    poll_email_targets,
)

# Queries
from app.services.crm.inbox.queries import (
    InboxQueries,
    get_channel_stats,
    get_inbox_stats,
    inbox_queries,
    list_inbox_conversations,
)

# SMTP server
from app.services.crm.inbox.smtp_inbound import start_smtp_server, stop_smtp_server

__all__ = [
    "EmailPoller",
    # Orchestrator
    "InboxOperations",
    # Queries
    "InboxQueries",
    "_render_personalization",
    "_resolve_connector_config",
    "_resolve_integration_target",
    "_resolve_person_for_contact",
    "_smtp_config_from_connector",
    # Connectors
    "create_email_connector_target",
    "create_whatsapp_connector_target",
    "enqueue_outbound_message",
    "ensure_email_polling_job",
    "get_channel_stats",
    "get_inbox_stats",
    "inbox_operations",
    "inbox_queries",
    "list_inbox_conversations",
    # Email polling
    "poll_email_inbox",
    "poll_email_targets",
    "receive_chat_message",
    # Inbound
    "receive_email_message",
    "receive_sms_message",
    "receive_whatsapp_message",
    # Outbound
    "send_message",
    "send_message_with_retry",
    "send_outbound_message",
    "send_reply",
    # SMTP
    "start_smtp_server",
    "stop_smtp_server",
]
