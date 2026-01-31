"""WebSocket endpoint for widget visitors."""

from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.logging import get_logger
from app.websocket.events import EventType, WebSocketEvent
from app.websocket.manager import get_connection_manager
from app.websocket.widget_auth import authenticate_widget_visitor

logger = get_logger(__name__)

router = APIRouter(tags=["websocket-widget"])


class WidgetInboundMessageType:
    """Types of messages widget clients can send."""

    MESSAGE = "message"
    TYPING = "typing"
    PING = "ping"
    READ = "read"
    SUBSCRIBE = "subscribe"


@router.websocket("/ws/widget")
async def widget_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for widget visitors.

    Authentication: ?token={visitor_token} query param

    Client messages:
    - {type: "message", body: "text"} - Send a message (handled via REST API for now)
    - {type: "typing", is_typing: true|false} - Typing indicator
    - {type: "ping"} - Keep-alive ping
    - {type: "read"} - Mark messages as read

    Server events:
    - message_new - New message in conversation
    - message_status_changed - Message status update
    - user_typing - Agent is typing
    - connection_ack - Connection established
    - heartbeat - Ping response
    """
    await websocket.accept()

    # Authenticate
    auth_result = await authenticate_widget_visitor(websocket)
    if not auth_result:
        return

    session_id = auth_result["session_id"]
    conversation_id = auth_result.get("conversation_id")
    manager = get_connection_manager()

    # Register connection using session_id as the "user_id"
    # This allows us to target specific widget sessions
    await manager.register_connection(f"widget:{session_id}", websocket)

    # Subscribe to conversation updates if conversation exists
    if conversation_id:
        await manager.subscribe_conversation(f"widget:{session_id}", conversation_id)

    try:
        while True:
            data = await websocket.receive_text()
            await _handle_widget_message(
                session_id,
                conversation_id,
                websocket,
                data,
                manager,
            )
    except WebSocketDisconnect:
        logger.debug("widget_websocket_disconnected session_id=%s", session_id)
    except Exception as exc:
        logger.warning("widget_websocket_error session_id=%s error=%s", session_id, exc)
    finally:
        await manager.unregister_connection(f"widget:{session_id}", websocket)


async def _handle_widget_message(
    session_id: str,
    conversation_id: str | None,
    websocket: WebSocket,
    raw_data: str,
    manager,
):
    """Process incoming widget client message."""
    try:
        data = json.loads(raw_data)
        msg_type = data.get("type")

        if msg_type == WidgetInboundMessageType.PING:
            # Respond with heartbeat
            heartbeat = WebSocketEvent(
                event=EventType.HEARTBEAT,
                data={"status": "ok"},
            )
            await websocket.send_json(heartbeat.model_dump(mode="json"))

        elif msg_type == WidgetInboundMessageType.TYPING:
            # Broadcast typing indicator to agents watching the conversation
            if conversation_id:
                typing_event = WebSocketEvent(
                    event=EventType.USER_TYPING,
                    data={
                        "session_id": session_id,
                        "conversation_id": conversation_id,
                        "is_typing": data.get("is_typing", True),
                        "is_visitor": True,
                    },
                )
                await manager.broadcast_to_conversation(conversation_id, typing_event)

        elif msg_type == WidgetInboundMessageType.READ:
            # Mark messages as read (handled via REST API typically)
            pass

        elif msg_type == WidgetInboundMessageType.MESSAGE:
            # Messages should be sent via REST API for proper persistence
            # This is just for acknowledgment
            logger.debug(
                "widget_message_via_ws session_id=%s (use REST API for messages)",
                session_id,
            )

        elif msg_type == WidgetInboundMessageType.SUBSCRIBE:
            # Client-initiated subscription (e.g., after reconnect)
            conv_id = data.get("conversation_id")
            if conv_id:
                await manager.subscribe_conversation(f"widget:{session_id}", conv_id)
                ack = WebSocketEvent(
                    event=EventType.CONNECTION_ACK,
                    data={"subscribed_to": conv_id},
                )
                await websocket.send_json(ack.model_dump(mode="json"))
                logger.debug(
                    "widget_subscribed_via_ws session_id=%s conversation_id=%s",
                    session_id,
                    conv_id,
                )

    except json.JSONDecodeError:
        logger.warning("widget_websocket_invalid_json session_id=%s", session_id)
    except Exception as exc:
        logger.warning("widget_websocket_message_error session_id=%s error=%s", session_id, exc)
