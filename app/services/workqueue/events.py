"""Event emit helpers for the Workqueue WebSocket channel."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Iterable, Literal
from uuid import UUID

logger = logging.getLogger(__name__)

ChangeKind = Literal["added", "removed", "updated"]


def user_channel(user_id: UUID) -> str:
    return f"workqueue:user:{user_id}"


def team_channel(team_id: UUID) -> str:
    return f"workqueue:audience:team:{team_id}"


def org_channel() -> str:
    return "workqueue:audience:org"


def _publish(channel: str, payload: dict) -> None:
    """Publish via the existing sync Redis client used by inbox notifications."""
    from app.websocket.broadcaster import _publish_sync

    _publish_sync(channel, payload)


def emit_change(
    *,
    kind,
    item_id: UUID,
    change: ChangeKind,
    affected_user_ids: Iterable[UUID] = (),
    affected_team_ids: Iterable[UUID] = (),
    affected_org: bool = False,
    score: int | None = None,
    reason: str | None = None,
) -> None:
    payload = {
        "type": "workqueue.changed",
        "kind": kind.value if hasattr(kind, "value") else str(kind),
        "item_id": str(item_id),
        "change": change,
        "score": score,
        "reason": reason,
        "happened_at": datetime.now(UTC).isoformat(),
    }

    targets: list[str] = []
    targets.extend(user_channel(uid) for uid in affected_user_ids)
    targets.extend(team_channel(tid) for tid in affected_team_ids)
    if affected_org:
        targets.append(org_channel())

    for channel in targets:
        try:
            _publish(channel, payload)
        except Exception as exc:
            logger.warning("workqueue_emit_failed channel=%s error=%s", channel, exc)
