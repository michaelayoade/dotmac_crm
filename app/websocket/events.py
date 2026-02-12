from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class EventType(StrEnum):
    """WebSocket event types for the inbox."""

    MESSAGE_NEW = "message_new"
    MESSAGE_STATUS_CHANGED = "message_status_changed"
    CONVERSATION_UPDATED = "conversation_updated"
    CONVERSATION_CREATED = "conversation_created"
    CONVERSATION_SUMMARY = "conversation_summary"
    USER_TYPING = "user_typing"
    CONNECTION_ACK = "connection_ack"
    HEARTBEAT = "heartbeat"
    AGENT_NOTIFICATION = "agent_notification"
    INBOX_UPDATED = "inbox_updated"


class WebSocketEvent(BaseModel):
    """Outbound WebSocket event sent to clients."""

    event: EventType
    data: dict[str, Any]
    timestamp: datetime | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.timestamp is None:
            object.__setattr__(self, "timestamp", datetime.now(UTC))


class InboundMessageType(StrEnum):
    """Types of messages clients can send."""

    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    TYPING = "typing"
    PING = "ping"


class InboundMessage(BaseModel):
    """Message received from WebSocket client."""

    type: InboundMessageType
    conversation_id: str | None = None
    data: dict[str, Any] | None = None
