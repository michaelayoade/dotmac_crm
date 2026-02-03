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
from app.services.crm.inbox.orchestrator import InboxOperations, inbox_operations

# Inbound message processing
from app.services.crm.inbox.inbound import (
    receive_email_message,
    receive_sms_message,
    receive_whatsapp_message,
    receive_chat_message,
)

# Outbound messaging
from app.services.crm.inbox.outbound import (
    send_message,
    send_reply,
    send_outbound_message,
)

# Email polling
from app.services.crm.inbox.email_polling import poll_email_inbox, EmailPoller
from app.services.crm.inbox.polling import (
    ensure_email_polling_job,
    poll_email_targets,
)

# Connector creation and SMTP config
from app.services.crm.inbox.connectors_create import (
    create_email_connector_target,
    create_whatsapp_connector_target,
)
from app.services.crm.inbox.connectors import _smtp_config_from_connector

# SMTP server
from app.services.crm.inbox.smtp_inbound import start_smtp_server, stop_smtp_server

# Queries
from app.services.crm.inbox.queries import (
    InboxQueries,
    inbox_queries,
    list_inbox_conversations,
    get_inbox_stats,
    get_channel_stats,
)

__all__ = [
    # Orchestrator
    "InboxOperations",
    "inbox_operations",
    # Inbound
    "receive_email_message",
    "receive_sms_message",
    "receive_whatsapp_message",
    "receive_chat_message",
    # Outbound
    "send_message",
    "send_reply",
    "send_outbound_message",
    # Email polling
    "poll_email_inbox",
    "EmailPoller",
    "ensure_email_polling_job",
    "poll_email_targets",
    # Connectors
    "create_email_connector_target",
    "create_whatsapp_connector_target",
    "_smtp_config_from_connector",
    # SMTP
    "start_smtp_server",
    "stop_smtp_server",
    # Queries
    "InboxQueries",
    "inbox_queries",
    "list_inbox_conversations",
    "get_inbox_stats",
    "get_channel_stats",
]
