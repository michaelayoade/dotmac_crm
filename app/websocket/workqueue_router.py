"""WebSocket endpoint for the Workqueue real-time channel.

Clients send ``{"type": "subscribe", "channels": [...]}`` after the connection
is established. Each requested channel is authorized against the authenticated
user's identity and permissions; only allowed channels are subscribed to.

Channels follow the ``workqueue:*`` shape produced by
``app.services.workqueue.events``:

* ``workqueue:user:{uuid}`` -- only the user themselves
* ``workqueue:audience:team:{uuid}`` -- requires ``workqueue:audience:team`` perm
* ``workqueue:audience:org`` -- requires ``workqueue:audience:org`` perm

This module deliberately avoids the inbox ``ConnectionManager`` because the
inbox listener subscribes to a different Redis prefix (``inbox_ws:*``) and has
no concept of arbitrary per-connection channel sets.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import Iterable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.db import SessionLocal
from app.logging import get_logger
from app.services.auth_flow import _load_rbac_claims
from app.websocket.auth import authenticate_websocket

logger = get_logger(__name__)

router = APIRouter(tags=["websocket-workqueue"])

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

WORKQUEUE_USER_PREFIX = "workqueue:user:"
WORKQUEUE_TEAM_PREFIX = "workqueue:audience:team:"
WORKQUEUE_ORG_CHANNEL = "workqueue:audience:org"

PERM_AUDIENCE_TEAM = "workqueue:audience:team"
PERM_AUDIENCE_ORG = "workqueue:audience:org"


def is_subscription_allowed(
    *,
    user_id: str,
    permissions: Iterable[str],
    channel: str,
) -> bool:
    """Return True iff the user may subscribe to the given workqueue channel.

    Pure / side-effect-free so it can be unit tested in isolation.
    """
    if not channel:
        return False
    perms = set(permissions or [])

    if channel == WORKQUEUE_ORG_CHANNEL:
        return PERM_AUDIENCE_ORG in perms

    if channel.startswith(WORKQUEUE_TEAM_PREFIX):
        # Team membership check is a v2 concern; gate on permission alone.
        return PERM_AUDIENCE_TEAM in perms

    if channel.startswith(WORKQUEUE_USER_PREFIX):
        requested = channel[len(WORKQUEUE_USER_PREFIX) :]
        return bool(user_id) and str(user_id) == requested

    return False


def _load_user_permissions(person_id: str) -> set[str]:
    """Load RBAC permissions for the WS user via a short-lived session."""
    db = SessionLocal()
    try:
        _, perms = _load_rbac_claims(db, person_id)
        return set(perms)
    except Exception as exc:
        logger.warning("workqueue_ws_load_perms_error person_id=%s error=%s", person_id, exc)
        return set()
    finally:
        db.close()


@router.websocket("/ws/workqueue")
async def workqueue_websocket(websocket: WebSocket) -> None:
    """WebSocket endpoint for Workqueue real-time updates."""
    await websocket.accept()

    auth_result = await authenticate_websocket(websocket)
    if not auth_result:
        return

    user_id = auth_result["person_id"]
    permissions = _load_user_permissions(user_id)

    # State held per connection.
    pubsub = None
    redis_client = None
    listener_task: asyncio.Task | None = None
    subscribed: set[str] = set()
    heartbeat_task: asyncio.Task | None = None

    async def _ack(payload: dict) -> None:
        with contextlib.suppress(Exception):
            await websocket.send_json(payload)

    async def _ensure_redis():
        nonlocal pubsub, redis_client
        if pubsub is not None:
            return pubsub
        try:
            import redis.asyncio as aioredis

            redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
            pubsub = redis_client.pubsub()
            return pubsub
        except Exception as exc:
            logger.warning("workqueue_ws_redis_init_error error=%s", exc)
            return None

    async def _listen() -> None:
        try:
            if pubsub is None:
                return
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if not message:
                    continue
                if message.get("type") not in {"message", "pmessage"}:
                    continue
                data = message.get("data")
                if not data:
                    continue
                try:
                    payload = json.loads(data) if isinstance(data, str) else data
                except (TypeError, ValueError):
                    continue
                if websocket.client_state != WebSocketState.CONNECTED:
                    return
                try:
                    await websocket.send_json(payload)
                except Exception:
                    return
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("workqueue_ws_listener_error user_id=%s error=%s", user_id, exc)

    async def _heartbeat() -> None:
        try:
            while websocket.client_state == WebSocketState.CONNECTED:
                await asyncio.sleep(25)
                try:
                    await websocket.send_json({"type": "heartbeat"})
                except Exception:
                    return
        except asyncio.CancelledError:
            pass

    await _ack({"type": "connection_ack", "user_id": user_id})
    heartbeat_task = asyncio.create_task(_heartbeat())

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")

            if msg_type == "ping":
                await _ack({"type": "heartbeat"})
                continue

            if msg_type == "subscribe":
                requested = msg.get("channels") or []
                if not isinstance(requested, list):
                    continue
                allowed: list[str] = []
                denied: list[str] = []
                for ch in requested:
                    if not isinstance(ch, str):
                        continue
                    if ch in subscribed:
                        continue
                    if is_subscription_allowed(user_id=user_id, permissions=permissions, channel=ch):
                        allowed.append(ch)
                    else:
                        denied.append(ch)

                if allowed:
                    ps = await _ensure_redis()
                    if ps is not None:
                        try:
                            await ps.subscribe(*allowed)
                            subscribed.update(allowed)
                        except Exception as exc:
                            logger.warning(
                                "workqueue_ws_subscribe_error user_id=%s error=%s",
                                user_id,
                                exc,
                            )
                        if listener_task is None or listener_task.done():
                            listener_task = asyncio.create_task(_listen())

                await _ack(
                    {
                        "type": "subscribe_ack",
                        "subscribed": sorted(subscribed),
                        "denied": denied,
                    }
                )
                continue

            if msg_type == "unsubscribe":
                requested = msg.get("channels") or []
                if not isinstance(requested, list):
                    continue
                to_drop = [c for c in requested if isinstance(c, str) and c in subscribed]
                if to_drop and pubsub is not None:
                    try:
                        await pubsub.unsubscribe(*to_drop)
                    except Exception as exc:
                        logger.warning(
                            "workqueue_ws_unsubscribe_error user_id=%s error=%s",
                            user_id,
                            exc,
                        )
                    for ch in to_drop:
                        subscribed.discard(ch)
                await _ack({"type": "unsubscribe_ack", "subscribed": sorted(subscribed)})
                continue

    except WebSocketDisconnect:
        logger.debug("workqueue_ws_disconnected user_id=%s", user_id)
    except Exception as exc:
        logger.warning("workqueue_ws_error user_id=%s error=%s", user_id, exc)
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
        if listener_task:
            listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await listener_task
        if pubsub is not None:
            try:
                if subscribed:
                    await pubsub.unsubscribe(*subscribed)
                await pubsub.close()
            except Exception:  # nosec B110 - best-effort websocket cleanup
                pass
        if redis_client is not None:
            with contextlib.suppress(Exception):
                await redis_client.close()
