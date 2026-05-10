"""Snooze CRUD for the Workqueue."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.workqueue import WorkqueueItemKind, WorkqueueSnooze
from app.services.workqueue.types import ItemKind


def _to_db_kind(kind: ItemKind) -> WorkqueueItemKind:
    return WorkqueueItemKind(kind.value)


class WorkqueueSnoozeService:
    @staticmethod
    def snooze(
        db: Session,
        user_id: UUID,
        kind: ItemKind,
        item_id: UUID,
        *,
        until: datetime | None = None,
        until_next_reply: bool = False,
    ) -> WorkqueueSnooze:
        if (until is None) == (until_next_reply is False):
            raise ValueError("Exactly one of `until` or `until_next_reply` must be provided")

        existing = (
            db.query(WorkqueueSnooze)
            .filter(
                WorkqueueSnooze.user_id == user_id,
                WorkqueueSnooze.item_kind == _to_db_kind(kind),
                WorkqueueSnooze.item_id == item_id,
            )
            .one_or_none()
        )
        if existing is None:
            existing = WorkqueueSnooze(
                user_id=user_id,
                item_kind=_to_db_kind(kind),
                item_id=item_id,
            )
            db.add(existing)

        existing.snooze_until = until
        existing.until_next_reply = until_next_reply
        db.commit()
        db.refresh(existing)
        return existing

    @staticmethod
    def clear(db: Session, user_id: UUID, kind: ItemKind, item_id: UUID) -> int:
        deleted = (
            db.query(WorkqueueSnooze)
            .filter(
                WorkqueueSnooze.user_id == user_id,
                WorkqueueSnooze.item_kind == _to_db_kind(kind),
                WorkqueueSnooze.item_id == item_id,
            )
            .delete(synchronize_session=False)
        )
        db.commit()
        return deleted

    @staticmethod
    def active_snoozed_ids(db: Session, user_id: UUID) -> dict[ItemKind, set[UUID]]:
        now = datetime.now(UTC)
        rows = (
            db.query(WorkqueueSnooze.item_kind, WorkqueueSnooze.item_id)
            .filter(
                WorkqueueSnooze.user_id == user_id,
                or_(
                    WorkqueueSnooze.until_next_reply.is_(True),
                    and_(
                        WorkqueueSnooze.snooze_until.isnot(None),
                        WorkqueueSnooze.snooze_until > now,
                    ),
                ),
            )
            .all()
        )
        result: dict[ItemKind, set[UUID]] = {k: set() for k in ItemKind}
        for db_kind, item_id in rows:
            result[ItemKind(db_kind.value)].add(item_id)
        return result

    @staticmethod
    def clear_until_next_reply_for_conversation(db: Session, conversation_id: UUID) -> list[UUID]:
        rows = (
            db.query(WorkqueueSnooze)
            .filter(
                WorkqueueSnooze.item_kind == WorkqueueItemKind.conversation,
                WorkqueueSnooze.item_id == conversation_id,
                WorkqueueSnooze.until_next_reply.is_(True),
            )
            .all()
        )
        affected = [row.user_id for row in rows]
        for row in rows:
            db.delete(row)
        if affected:
            db.commit()
        return affected


workqueue_snooze = WorkqueueSnoozeService()
