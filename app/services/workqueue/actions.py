"""Workqueue inline actions — facade over domain managers.

This module is a thin dispatcher: snooze/clear-snooze go to ``workqueue_snooze``
and claim/complete delegate to the domain manager that owns each ItemKind.
No business logic lives here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.workqueue.snooze import workqueue_snooze
from app.services.workqueue.types import ItemKind

_COMPLETE_DISALLOWED = {ItemKind.lead, ItemKind.quote}


def _require_perm(user, permission: str) -> None:
    if permission not in user.permissions:
        raise PermissionError(f"Missing permission: {permission}")


class WorkqueueActions:
    @staticmethod
    def snooze(
        db: Session,
        user,
        kind: ItemKind,
        item_id: UUID,
        *,
        until: datetime | None = None,
        until_next_reply: bool = False,
    ) -> None:
        workqueue_snooze.snooze(
            db,
            user.person_id,
            kind,
            item_id,
            until=until,
            until_next_reply=until_next_reply,
        )

    @staticmethod
    def snooze_preset(
        db: Session,
        user,
        kind: ItemKind,
        item_id: UUID,
        preset: str,
    ) -> None:
        now = datetime.now(UTC)
        if preset == "1h":
            workqueue_snooze.snooze(db, user.person_id, kind, item_id, until=now + timedelta(hours=1))
        elif preset == "tomorrow":
            target = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
            workqueue_snooze.snooze(db, user.person_id, kind, item_id, until=target)
        elif preset == "next_week":
            workqueue_snooze.snooze(db, user.person_id, kind, item_id, until=now + timedelta(days=7))
        elif preset == "next_reply":
            if kind is not ItemKind.conversation:
                raise ValueError("until_next_reply only valid for conversations")
            workqueue_snooze.snooze(db, user.person_id, kind, item_id, until_next_reply=True)
        else:
            raise ValueError(f"Unknown preset: {preset}")

    @staticmethod
    def clear_snooze(db: Session, user, kind: ItemKind, item_id: UUID) -> None:
        workqueue_snooze.clear(db, user.person_id, kind, item_id)

    @staticmethod
    def is_snoozed(db: Session, user_id: UUID, kind: ItemKind, item_id: UUID) -> bool:
        ids = workqueue_snooze.active_snoozed_ids(db, user_id)
        return item_id in ids.get(kind, set())

    @staticmethod
    def claim(db: Session, user, kind: ItemKind, item_id: UUID) -> None:
        _require_perm(user, "workqueue:claim")
        if kind is ItemKind.ticket:
            from app.services.tickets import tickets

            tickets.assign(db, item_id, user.person_id, actor_id=user.person_id)
        elif kind is ItemKind.conversation:
            # TODO: dispatch to conversation assignment service once a thin
            # ``assign(db, conversation_id, person_id, actor_id=...)`` facade
            # exists. The current `ConversationAssignment` model uses
            # team/agent IDs (not person IDs) so this is non-trivial.
            raise NotImplementedError("claim for conversation not yet wired — needs an assignment facade")
        elif kind in (ItemKind.lead, ItemKind.quote):
            # TODO: leads use ``owner_agent_id`` (CrmAgent), not person_id.
            # Wire claim once a person-id-aware facade is exposed.
            raise NotImplementedError(f"claim for {kind.value} not yet wired — owner is an agent, not a person")
        elif kind is ItemKind.task:
            # TODO: ProjectTasks has no ``assign`` facade today; would need
            # to delegate to ``project_tasks.update`` with assignee field.
            raise NotImplementedError("claim for task not yet wired — add a project_tasks.assign facade")
        else:
            raise ValueError(f"claim not supported for {kind}")

    @staticmethod
    def complete(db: Session, user, kind: ItemKind, item_id: UUID) -> None:
        if kind in _COMPLETE_DISALLOWED:
            raise ValueError(f"complete not allowed for {kind.value} — use the record's stage controls")
        if kind is ItemKind.ticket:
            from app.services.tickets import tickets

            tickets.resolve(db, item_id, actor_id=user.person_id)
        elif kind is ItemKind.conversation:
            # TODO: wire to a conversation status-transition facade
            # (``set_status(closed)``) once it exists.
            raise NotImplementedError("complete for conversation not yet wired — needs a status facade")
        elif kind is ItemKind.task:
            # TODO: wire to project_tasks complete facade once available.
            raise NotImplementedError("complete for task not yet wired — needs a project_tasks.complete facade")
        else:
            raise ValueError(f"complete not supported for {kind}")


workqueue_actions = WorkqueueActions()
