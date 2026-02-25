"""Tests for inbox websocket message handling."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.websocket.events import EventType
from app.websocket.router import _handle_client_message


def _mock_manager():
    return SimpleNamespace(
        subscribe_conversation=AsyncMock(),
        unsubscribe_conversation=AsyncMock(),
        broadcast_to_conversation=AsyncMock(),
        send_heartbeat=AsyncMock(),
    )


def test_presence_event_broadcasts_replying_state():
    manager = _mock_manager()
    websocket = object()

    asyncio.run(
        _handle_client_message(
            "person-1",
            websocket,
            json.dumps(
                {
                    "type": "presence",
                    "conversation_id": "conv-1",
                    "data": {"state": "replying", "active": True},
                }
            ),
            manager,
        )
    )

    manager.broadcast_to_conversation.assert_awaited_once()
    conversation_id, event = manager.broadcast_to_conversation.await_args.args
    assert conversation_id == "conv-1"
    assert event.event == EventType.USER_PRESENCE
    assert event.data["user_id"] == "person-1"
    assert event.data["conversation_id"] == "conv-1"
    assert event.data["state"] == "replying"
    assert event.data["active"] is True


def test_presence_event_parses_false_string_active():
    manager = _mock_manager()
    websocket = object()

    asyncio.run(
        _handle_client_message(
            "person-2",
            websocket,
            json.dumps(
                {
                    "type": "presence",
                    "conversation_id": "conv-2",
                    "data": {"state": "viewing", "active": "false"},
                }
            ),
            manager,
        )
    )

    manager.broadcast_to_conversation.assert_awaited_once()
    _, event = manager.broadcast_to_conversation.await_args.args
    assert event.event == EventType.USER_PRESENCE
    assert event.data["active"] is False
