from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.logging import get_logger
from app.websocket.events import EventType, WebSocketEvent
from app.websocket.manager import get_connection_manager

if TYPE_CHECKING:
    from app.models.crm.conversation import Conversation, Message

logger = get_logger(__name__)


def _handle_task_exception(task: asyncio.Task):
    """Callback to log exceptions from background tasks."""
    try:
        exc = task.exception()
        if exc:
            logger.error("websocket_task_error error=%s", exc, exc_info=exc)
    except asyncio.CancelledError:
        pass


def _run_async(coro):
    """Run an async coroutine from sync code with error handling."""
    try:
        asyncio.get_running_loop()
        task = asyncio.create_task(coro)
        task.add_done_callback(_handle_task_exception)
    except RuntimeError:
        try:
            asyncio.run(coro)
        except Exception as exc:
            logger.error("async_run_error error=%s", exc)


def _ensure_manager_connected(manager) -> None:
    try:
        if getattr(manager, "_redis_client", None) is None:
            _run_async(manager.connect())
    except Exception as exc:
        logger.warning("websocket_manager_connect_error error=%s", exc)


def broadcast_new_message(message: Message, conversation: Conversation):
    """
    Broadcast a new message event to conversation subscribers.

    Called from inbox.py after creating a new message.
    """
    try:
        event = WebSocketEvent(
            event=EventType.MESSAGE_NEW,
            data={
                "message_id": str(message.id),
                "conversation_id": str(conversation.id),
                "channel_type": message.channel_type.value if message.channel_type else None,
                "direction": message.direction.value if message.direction else None,
                "status": message.status.value if message.status else None,
                "body_preview": (message.body[:100] + "...")
                if message.body and len(message.body) > 100
                else message.body,
                "subject": message.subject,
                "person_id": str(conversation.person_id) if conversation.person_id else None,
            },
        )
        manager = get_connection_manager()
        _ensure_manager_connected(manager)
        _run_async(manager.broadcast_to_conversation(str(conversation.id), event))
        logger.debug(
            "broadcast_new_message conversation_id=%s message_id=%s",
            conversation.id,
            message.id,
        )
    except Exception as exc:
        logger.warning("broadcast_new_message_error error=%s", exc)


def broadcast_message_status(message_id: str, conversation_id: str, status: str):
    """
    Broadcast a message status change event.

    Called from inbox.py after updating message status.
    """
    try:
        event = WebSocketEvent(
            event=EventType.MESSAGE_STATUS_CHANGED,
            data={
                "message_id": str(message_id),
                "conversation_id": str(conversation_id),
                "status": status,
            },
        )
        manager = get_connection_manager()
        _ensure_manager_connected(manager)
        _run_async(manager.broadcast_to_conversation(str(conversation_id), event))
        logger.debug(
            "broadcast_message_status conversation_id=%s message_id=%s status=%s",
            conversation_id,
            message_id,
            status,
        )
    except Exception as exc:
        logger.warning("broadcast_message_status_error error=%s", exc)


def broadcast_conversation_updated(conversation: Conversation):
    """
    Broadcast a conversation update event.

    Called when conversation metadata changes (status, assignee, etc).
    """
    try:
        event = WebSocketEvent(
            event=EventType.CONVERSATION_UPDATED,
            data={
                "conversation_id": str(conversation.id),
                "status": conversation.status.value if conversation.status else None,
                "is_active": conversation.is_active,
                "person_id": str(conversation.person_id) if conversation.person_id else None,
            },
        )
        manager = get_connection_manager()
        _ensure_manager_connected(manager)
        _run_async(manager.broadcast_to_conversation(str(conversation.id), event))
        logger.debug("broadcast_conversation_updated conversation_id=%s", conversation.id)
    except Exception as exc:
        logger.warning("broadcast_conversation_updated_error error=%s", exc)


def broadcast_conversation_summary(conversation_id: str, summary: dict):
    """Broadcast a lightweight conversation summary update."""
    try:
        payload = dict(summary)
        payload["conversation_id"] = str(conversation_id)
        event = WebSocketEvent(
            event=EventType.CONVERSATION_SUMMARY,
            data=payload,
        )
        manager = get_connection_manager()
        _ensure_manager_connected(manager)
        _run_async(manager.broadcast_to_conversation(str(conversation_id), event))
    except Exception as exc:
        logger.warning("broadcast_conversation_summary_error error=%s", exc)


def broadcast_to_widget_visitor(session_id: str, message: Message):
    """
    Broadcast a message to a specific widget visitor's connections.

    Used when an agent sends a message to a widget conversation.
    """
    try:
        event = WebSocketEvent(
            event=EventType.MESSAGE_NEW,
            data={
                "message_id": str(message.id),
                "conversation_id": str(message.conversation_id),
                "channel_type": message.channel_type.value if message.channel_type else None,
                "direction": message.direction.value if message.direction else None,
                "status": message.status.value if message.status else None,
                "body": message.body,
                "author_name": (
                    message.author.display_name or f"{message.author.first_name} {message.author.last_name}"
                    if message.author
                    else None
                ),
                "created_at": message.created_at.isoformat() if message.created_at else None,
            },
        )
        manager = get_connection_manager()
        _ensure_manager_connected(manager)
        # Widget connections are registered with "widget:{session_id}" prefix
        _run_async(manager.broadcast_to_user(f"widget:{session_id}", event))
        logger.debug(
            "broadcast_to_widget_visitor session_id=%s message_id=%s",
            session_id,
            message.id,
        )
    except Exception as exc:
        logger.warning("broadcast_to_widget_visitor_error error=%s", exc)


def subscribe_widget_to_conversation(session_id: str, conversation_id: str):
    """
    Subscribe widget visitor to their conversation and notify them.

    Called when a conversation is created for a widget session.
    """
    try:
        manager = get_connection_manager()
        _ensure_manager_connected(manager)
        _run_async(manager.subscribe_conversation(f"widget:{session_id}", conversation_id))
        event = WebSocketEvent(
            event=EventType.CONVERSATION_CREATED,
            data={"conversation_id": conversation_id},
        )
        _run_async(manager.broadcast_to_user(f"widget:{session_id}", event))
        logger.debug(
            "widget_auto_subscribed session_id=%s conversation_id=%s",
            session_id,
            conversation_id,
        )
    except Exception as exc:
        logger.warning("widget_auto_subscribe_error error=%s", exc)


def broadcast_agent_notification(user_id: str, payload: dict):
    """Send a notification event directly to an agent's inbox connections."""
    try:
        event = WebSocketEvent(
            event=EventType.AGENT_NOTIFICATION,
            data=payload,
        )
        manager = get_connection_manager()
        _ensure_manager_connected(manager)
        logger.info(
            "agent_notification_broadcast user_id=%s conversation_id=%s kind=%s",
            user_id,
            payload.get("conversation_id"),
            payload.get("kind"),
        )
        _run_async(manager.broadcast_to_user(str(user_id), event))
        logger.debug("broadcast_agent_notification user_id=%s", user_id)
    except Exception as exc:
        logger.warning("broadcast_agent_notification_error error=%s", exc)


def broadcast_inbox_updated(user_id: str, payload: dict):
    """Send a lightweight inbox refresh event to a specific user."""
    try:
        event = WebSocketEvent(
            event=EventType.INBOX_UPDATED,
            data=payload,
        )
        manager = get_connection_manager()
        _ensure_manager_connected(manager)
        _run_async(manager.broadcast_to_user(str(user_id), event))
        logger.info("broadcast_inbox_updated user_id=%s", user_id)
    except Exception as exc:
        logger.warning("broadcast_inbox_updated_error error=%s", exc)
