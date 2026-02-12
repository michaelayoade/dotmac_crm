from __future__ import annotations

import asyncio
import contextlib
import json
import os
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.logging import get_logger
from app.websocket.events import EventType, WebSocketEvent

logger = get_logger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CHANNEL_PREFIX = "inbox_ws:"


class ConnectionManager:
    """
    Manages WebSocket connections with Redis pub/sub for horizontal scaling.

    Local connection pool: user_id -> [WebSocket]
    Conversation subscriptions: conversation_id -> set[user_id]
    """

    def __init__(self) -> None:
        self._connections: dict[str, list[WebSocket]] = {}
        self._subscriptions: dict[str, set[str]] = {}
        self._redis_client: Any | None = None
        self._pubsub: Any | None = None
        self._listener_task: asyncio.Task | None = None
        self._heartbeat_tasks: dict[tuple[str, int], asyncio.Task] = {}
        self._running = False

    async def connect(self) -> None:
        """Initialize Redis connection and start listener."""
        try:
            import redis.asyncio as aioredis

            self._redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
            self._pubsub = self._redis_client.pubsub()
            await self._pubsub.psubscribe(f"{CHANNEL_PREFIX}*")
            self._running = True
            self._listener_task = asyncio.create_task(self._redis_listener())
            logger.info("websocket_manager_connected redis=%s", REDIS_URL)
        except Exception as exc:
            logger.warning("websocket_manager_redis_failed error=%s", exc)

    async def disconnect(self) -> None:
        """Cleanup Redis connection and stop listener."""
        self._running = False
        if self._listener_task:
            self._listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener_task
        if self._pubsub:
            await self._pubsub.punsubscribe()
            await self._pubsub.close()
        if self._redis_client:
            await self._redis_client.close()
        logger.info("websocket_manager_disconnected")

    async def _redis_listener(self) -> None:
        """Listen for messages from Redis pub/sub and dispatch to local connections."""
        try:
            if not self._pubsub:
                return
            pubsub = self._pubsub
            while self._running:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message["type"] == "pmessage":
                    channel = message["channel"]
                    data = message["data"]
                    await self._handle_redis_message(channel, data)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("websocket_redis_listener_error error=%s", exc)

    async def _handle_redis_message(self, channel: str, data: str) -> None:
        """Process incoming Redis message and dispatch to local connections."""
        try:
            logger.info("websocket_redis_message_received channel=%s", channel)
            payload = json.loads(data)
            conversation_id = payload.get("conversation_id")
            user_id = payload.get("user_id")
            event_data = payload.get("event")

            if user_id and event_data:
                await self._dispatch_to_user(user_id, event_data)
                return

            if conversation_id and event_data:
                await self._dispatch_to_subscribers(conversation_id, event_data)
        except Exception as exc:
            logger.warning("websocket_redis_message_error error=%s", exc)

    async def _dispatch_to_subscribers(self, conversation_id: str, event_data: dict) -> None:
        """Send event to all users subscribed to a conversation."""
        user_ids = self._subscriptions.get(conversation_id, set())

        for user_id in list(user_ids):
            websockets = self._connections.get(user_id, [])
            for ws in list(websockets):
                try:
                    if ws.client_state == WebSocketState.CONNECTED:
                        await ws.send_json(event_data)
                except Exception:
                    await self._remove_connection(user_id, ws)

    async def register_connection(self, user_id: str, websocket: WebSocket) -> None:
        """Register a new WebSocket connection for a user."""
        if user_id not in self._connections:
            self._connections[user_id] = []
        self._connections[user_id].append(websocket)
        logger.debug("websocket_registered user_id=%s", user_id)

        # Send connection acknowledgment
        ack_event = WebSocketEvent(
            event=EventType.CONNECTION_ACK,
            data={"user_id": user_id, "status": "connected"},
        )
        await websocket.send_json(ack_event.model_dump(mode="json"))
        self._start_heartbeat(user_id, websocket)

    async def unregister_connection(self, user_id: str, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        await self._remove_connection(user_id, websocket)

    async def _remove_connection(self, user_id: str, websocket: WebSocket) -> None:
        """Internal method to remove a connection and clean up subscriptions."""
        self._stop_heartbeat(user_id, websocket)
        if user_id in self._connections:
            if websocket in self._connections[user_id]:
                self._connections[user_id].remove(websocket)
            if not self._connections[user_id]:
                del self._connections[user_id]

        # Clean up user from all subscriptions if no more connections
        if user_id not in self._connections:
            for conv_id in list(self._subscriptions.keys()):
                self._subscriptions[conv_id].discard(user_id)
                if not self._subscriptions[conv_id]:
                    del self._subscriptions[conv_id]

        logger.debug("websocket_unregistered user_id=%s", user_id)

    def _start_heartbeat(self, user_id: str, websocket: WebSocket) -> None:
        key = (user_id, id(websocket))
        if key in self._heartbeat_tasks:
            return
        task = asyncio.create_task(self._heartbeat_loop(user_id, websocket))
        self._heartbeat_tasks[key] = task

    def _stop_heartbeat(self, user_id: str, websocket: WebSocket) -> None:
        key = (user_id, id(websocket))
        task = self._heartbeat_tasks.pop(key, None)
        if task:
            task.cancel()

    async def _heartbeat_loop(self, user_id: str, websocket: WebSocket) -> None:
        try:
            while websocket.client_state == WebSocketState.CONNECTED:
                await self.send_heartbeat(user_id, websocket)
                await asyncio.sleep(25)
        except asyncio.CancelledError:
            pass

    async def subscribe_conversation(self, user_id: str, conversation_id: str) -> None:
        """Subscribe a user to conversation updates."""
        if conversation_id not in self._subscriptions:
            self._subscriptions[conversation_id] = set()
        self._subscriptions[conversation_id].add(user_id)
        logger.debug(
            "websocket_subscribed user_id=%s conversation_id=%s",
            user_id,
            conversation_id,
        )

    async def unsubscribe_conversation(self, user_id: str, conversation_id: str) -> None:
        """Unsubscribe a user from conversation updates."""
        if conversation_id in self._subscriptions:
            self._subscriptions[conversation_id].discard(user_id)
            if not self._subscriptions[conversation_id]:
                del self._subscriptions[conversation_id]
        logger.debug(
            "websocket_unsubscribed user_id=%s conversation_id=%s",
            user_id,
            conversation_id,
        )

    async def broadcast_to_conversation(self, conversation_id: str, event: WebSocketEvent) -> None:
        """Broadcast an event to all subscribers of a conversation via Redis."""
        event_data = event.model_dump(mode="json")

        # Publish to Redis for cross-instance delivery
        # Redis listener will dispatch to local connections, so don't dispatch twice
        if self._redis_client:
            try:
                payload = json.dumps({"conversation_id": conversation_id, "event": event_data})
                await self._redis_client.publish(f"{CHANNEL_PREFIX}{conversation_id}", payload)
                return  # Redis will handle local delivery via listener
            except Exception as exc:
                logger.warning("websocket_broadcast_redis_error error=%s", exc)

        # Fallback: dispatch locally only if Redis is unavailable
        await self._dispatch_to_subscribers(conversation_id, event_data)

    async def broadcast_to_user(self, user_id: str, event: WebSocketEvent) -> None:
        """Send event directly to a specific user's connections."""
        event_data = event.model_dump(mode="json")

        # Publish to Redis for cross-instance delivery
        # Redis listener will dispatch to local connections, so don't dispatch twice
        if self._redis_client:
            try:
                payload = json.dumps({"user_id": user_id, "event": event_data})
                logger.debug("websocket_broadcast_user_redis_publish user_id=%s", user_id)
                await self._redis_client.publish(f"{CHANNEL_PREFIX}user:{user_id}", payload)
                return  # Redis will handle local delivery via listener
            except Exception as exc:
                logger.warning("websocket_broadcast_redis_error error=%s", exc)

        # Fallback: dispatch locally only if Redis is unavailable
        await self._dispatch_to_user(user_id, event_data)

    async def _dispatch_to_user(self, user_id: str, event_data: dict) -> None:
        """Send event to all local connections for a user."""
        websockets = self._connections.get(user_id, [])
        for ws in list(websockets):
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_json(event_data)
            except Exception:
                await self._remove_connection(user_id, ws)

    async def send_heartbeat(self, user_id: str, websocket: WebSocket) -> None:
        """Send heartbeat response to a specific connection."""
        heartbeat = WebSocketEvent(
            event=EventType.HEARTBEAT,
            data={"status": "ok"},
        )
        try:
            await websocket.send_json(heartbeat.model_dump(mode="json"))
        except Exception:
            await self._remove_connection(user_id, websocket)


# Singleton instance
_manager: ConnectionManager | None = None


def get_connection_manager() -> ConnectionManager:
    """Get the singleton ConnectionManager instance."""
    global _manager
    if _manager is None:
        _manager = ConnectionManager()
    return _manager
