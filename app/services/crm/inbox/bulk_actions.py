"""Bulk action helpers for CRM inbox conversation lists."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.crm.conversation import ConversationTag
from app.schemas.crm.conversation import ConversationTagCreate
from app.services.common import coerce_uuid
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox.audit import log_conversation_action
from app.services.crm.inbox.conversation_actions import assign_conversation
from app.services.crm.inbox.conversation_status import (
    update_conversation_priority,
    update_conversation_status,
)


@dataclass(frozen=True)
class BulkActionResult:
    kind: Literal["invalid_action", "success"]
    applied: int = 0
    skipped: int = 0
    failed: int = 0
    detail: str | None = None


def _unique_ids(conversation_ids: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in conversation_ids:
        value = (raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def apply_bulk_action(
    db: Session,
    *,
    conversation_ids: list[str],
    action: str,
    actor_id: str | None,
    current_agent_id: str | None = None,
    label: str | None = None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> BulkActionResult:
    ids = _unique_ids(conversation_ids)
    if not ids:
        return BulkActionResult(kind="success", skipped=0, failed=0, applied=0)

    action_key = (action or "").strip().lower()
    applied = 0
    skipped = 0
    failed = 0

    if action_key.startswith("status:"):
        target_status = action_key.split(":", 1)[1]
        if not target_status:
            return BulkActionResult(kind="invalid_action", detail="Status is required")
        for conversation_id in ids:
            status_result = update_conversation_status(
                db,
                conversation_id=conversation_id,
                new_status=target_status,
                actor_id=actor_id,
                roles=roles,
                scopes=scopes,
            )
            if status_result.kind == "updated":
                applied += 1
            elif status_result.kind in {"not_found", "invalid_transition", "invalid_status", "forbidden"}:
                skipped += 1
            else:
                failed += 1
        return BulkActionResult(kind="success", applied=applied, skipped=skipped, failed=failed)

    if action_key.startswith("priority:"):
        target_priority = action_key.split(":", 1)[1]
        if not target_priority:
            return BulkActionResult(kind="invalid_action", detail="Priority is required")
        for conversation_id in ids:
            priority_result = update_conversation_priority(
                db,
                conversation_id=conversation_id,
                priority=target_priority,
                actor_id=actor_id,
            )
            if priority_result.kind == "updated":
                applied += 1
            elif priority_result.kind in {"not_found", "invalid_priority"}:
                skipped += 1
            else:
                failed += 1
        return BulkActionResult(kind="success", applied=applied, skipped=skipped, failed=failed)

    if action_key == "assign:me":
        if not current_agent_id:
            return BulkActionResult(kind="invalid_action", detail="Current agent is required for assign:me")
        for conversation_id in ids:
            assign_result = assign_conversation(
                db,
                conversation_id=conversation_id,
                agent_id=current_agent_id,
                team_id=None,
                assigned_by_id=actor_id,
                roles=roles,
                scopes=scopes,
            )
            if assign_result.kind == "success":
                applied += 1
            elif assign_result.kind in {"forbidden", "not_found", "invalid_input"}:
                skipped += 1
            else:
                failed += 1
        return BulkActionResult(kind="success", applied=applied, skipped=skipped, failed=failed)

    if action_key in {"label:add", "label:remove"}:
        normalized_label = (label or "").strip()
        if not normalized_label:
            return BulkActionResult(kind="invalid_action", detail="Label is required")
        for conversation_id in ids:
            if action_key == "label:add":
                try:
                    conversation_service.ConversationTags.create(
                        db,
                        payload=ConversationTagCreate(
                            conversation_id=coerce_uuid(conversation_id), tag=normalized_label
                        ),
                    )
                    applied += 1
                except IntegrityError:
                    db.rollback()
                    skipped += 1
                except Exception:
                    failed += 1
            else:
                try:
                    removed = (
                        db.query(ConversationTag)
                        .filter(ConversationTag.conversation_id == coerce_uuid(conversation_id))
                        .filter(func.lower(ConversationTag.tag) == normalized_label.lower())
                        .delete()
                    )
                    db.commit()
                    if removed:
                        applied += 1
                    else:
                        skipped += 1
                except Exception:
                    db.rollback()
                    failed += 1
            log_conversation_action(
                db,
                action="bulk_label_action",
                conversation_id=conversation_id,
                actor_id=actor_id,
                metadata={"operation": action_key, "label": normalized_label},
            )
        return BulkActionResult(kind="success", applied=applied, skipped=skipped, failed=failed)

    return BulkActionResult(kind="invalid_action", detail="Unsupported bulk action")
