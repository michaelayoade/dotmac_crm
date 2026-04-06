from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.logging import get_logger
from app.websocket.auth import authenticate_websocket
from app.websocket.events import EventType, InboundMessage, InboundMessageType, WebSocketEvent
from app.websocket.manager import get_connection_manager

logger = get_logger(__name__)

router = APIRouter(tags=["websocket"])


def _is_expected_websocket_state_error(exc: Exception) -> bool:
    return 'WebSocket is not connected. Need to call "accept" first.' in str(exc)


@router.websocket("/ws/inbox")
async def inbox_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for real-time inbox updates.

    Client actions:
    - subscribe: Subscribe to conversation updates
    - unsubscribe: Unsubscribe from conversation
    - typing: Broadcast typing indicator
    - presence: Broadcast conversation viewer presence
    - ping: Keep-alive ping
    """
    await websocket.accept()

    # Authenticate
    auth_result = await authenticate_websocket(websocket)
    if not auth_result:
        return

    user_id = auth_result["person_id"]
    manager = get_connection_manager()

    # Register connection
    await manager.register_connection(user_id, websocket)

    try:
        while True:
            data = await websocket.receive_text()
            await _handle_client_message(user_id, websocket, data, manager)
    except WebSocketDisconnect:
        logger.debug("websocket_disconnected user_id=%s", user_id)
    except Exception as exc:
        if _is_expected_websocket_state_error(exc):
            logger.debug("websocket_error user_id=%s error=%s", user_id, exc)
        else:
            logger.warning("websocket_error user_id=%s error=%s", user_id, exc)
    finally:
        await manager.unregister_connection(user_id, websocket)


async def _handle_client_message(user_id: str, websocket: WebSocket, raw_data: str, manager):
    """Process incoming client message."""
    try:
        data = json.loads(raw_data)
        message = InboundMessage(**data)

        if message.type == InboundMessageType.SUBSCRIBE:
            if message.conversation_id:
                await manager.subscribe_conversation(user_id, message.conversation_id)

        elif message.type == InboundMessageType.UNSUBSCRIBE:
            if message.conversation_id:
                await manager.unsubscribe_conversation(user_id, message.conversation_id)

        elif message.type == InboundMessageType.TYPING:
            if message.conversation_id:
                typing_event = WebSocketEvent(
                    event=EventType.USER_TYPING,
                    data={
                        "user_id": user_id,
                        "conversation_id": message.conversation_id,
                        "is_typing": message.data.get("is_typing", True) if message.data else True,
                    },
                )
                await manager.broadcast_to_conversation(message.conversation_id, typing_event)

        elif message.type == InboundMessageType.PRESENCE:
            if message.conversation_id:
                state = "viewing"
                active = True
                if message.data:
                    requested_state = str(message.data.get("state") or "").strip().lower()
                    if requested_state in {"viewing", "replying"}:
                        state = requested_state
                    requested_active = message.data.get("active")
                    if requested_active is not None:
                        if isinstance(requested_active, bool):
                            active = requested_active
                        elif isinstance(requested_active, (int, float)):
                            active = bool(requested_active)
                        elif isinstance(requested_active, str):
                            active = requested_active.strip().lower() not in {
                                "",
                                "0",
                                "false",
                                "no",
                                "off",
                            }
                        else:
                            active = bool(requested_active)
                presence_event = WebSocketEvent(
                    event=EventType.USER_PRESENCE,
                    data={
                        "user_id": user_id,
                        "conversation_id": message.conversation_id,
                        "state": state,
                        "active": active,
                    },
                )
                await manager.broadcast_to_conversation(message.conversation_id, presence_event)

        elif message.type == InboundMessageType.PING:
            await manager.send_heartbeat(user_id, websocket)

    except json.JSONDecodeError:
        logger.warning("websocket_invalid_json user_id=%s", user_id)
    except Exception as exc:
        if _is_expected_websocket_state_error(exc):
            logger.debug("websocket_message_error user_id=%s error=%s", user_id, exc)
        else:
            logger.warning("websocket_message_error user_id=%s error=%s", user_id, exc)
