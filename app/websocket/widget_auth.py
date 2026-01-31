"""WebSocket authentication for widget visitors."""

from __future__ import annotations

from fastapi import WebSocket

from app.db import SessionLocal
from app.logging import get_logger
from app.services.crm.chat_widget import widget_visitors

logger = get_logger(__name__)


async def authenticate_widget_visitor(websocket: WebSocket) -> dict | None:
    """
    Authenticate WebSocket connection for widget visitor.

    Extracts visitor_token from query param (?token=).
    Returns {session_id, widget_config_id, person_id} if valid, None otherwise.
    """
    token = websocket.query_params.get("token")

    if not token:
        await websocket.close(code=4001, reason="Visitor token required")
        return None

    db = SessionLocal()
    try:
        session = widget_visitors.get_session_by_token(db, token)

        if not session:
            await websocket.close(code=4001, reason="Invalid visitor token")
            return None

        # Validate widget config is still active
        config = session.widget_config
        if not config or not config.is_active:
            await websocket.close(code=4003, reason="Widget not available")
            return None

        return {
            "session_id": str(session.id),
            "widget_config_id": str(session.widget_config_id),
            "person_id": str(session.person_id) if session.person_id else None,
            "conversation_id": str(session.conversation_id) if session.conversation_id else None,
        }
    except Exception as e:
        logger.warning("widget_websocket_auth_error error=%s", e)
        await websocket.close(code=4001, reason="Authentication failed")
        return None
    finally:
        db.close()
