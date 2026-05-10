"""Event emit helpers for the Workqueue WebSocket channel."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Literal
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
    kind_label = kind.value if hasattr(kind, "value") else str(kind)
    payload = {
        "type": "workqueue.changed",
        "kind": kind_label,
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

    if targets:
        # Defensive: metrics must never break the emit pipeline.
        try:
            from app.metrics import observe_workqueue_ws_event

            observe_workqueue_ws_event(kind=kind_label, change=change, count=len(targets))
        except Exception:  # pragma: no cover — metrics are best-effort
            logger.debug("workqueue_metrics_failed kind=%s change=%s", kind_label, change)

    for channel in targets:
        try:
            _publish(channel, payload)
        except Exception as exc:
            logger.warning("workqueue_emit_failed channel=%s error=%s", channel, exc)
