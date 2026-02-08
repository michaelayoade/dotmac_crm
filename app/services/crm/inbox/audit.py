"""Audit helpers for CRM inbox actions."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services.audit_helpers import log_audit_event


def log_conversation_action(
    db: Session,
    *,
    action: str,
    conversation_id: str,
    actor_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    log_audit_event(
        db,
        request=None,
        action=action,
        entity_type="crm_inbox_conversation",
        entity_id=conversation_id,
        actor_id=actor_id,
        metadata=metadata,
    )


def log_comment_action(
    db: Session,
    *,
    action: str,
    comment_id: str,
    actor_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    log_audit_event(
        db,
        request=None,
        action=action,
        entity_type="crm_inbox_comment",
        entity_id=comment_id,
        actor_id=actor_id,
        metadata=metadata,
    )


def log_note_action(
    db: Session,
    *,
    action: str,
    note_id: str,
    actor_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    log_audit_event(
        db,
        request=None,
        action=action,
        entity_type="crm_inbox_note",
        entity_id=note_id,
        actor_id=actor_id,
        metadata=metadata,
    )
