"""Managed conversation label catalog for inbox UI and analytics."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.crm.conversation import ConversationTag
from app.models.crm.conversation_label import ConversationLabel
from app.services.common import coerce_uuid

_ALLOWED_COLORS = {
    "slate",
    "blue",
    "indigo",
    "violet",
    "emerald",
    "teal",
    "amber",
    "orange",
    "rose",
    "red",
}


@dataclass(frozen=True)
class LabelActionResult:
    ok: bool
    error_detail: str | None = None


def _normalize_name(value: str | None) -> str:
    name = (value or "").strip()
    if not name:
        raise ValueError("Label name is required")
    if len(name) > 80:
        raise ValueError("Label name must be 80 characters or fewer")
    return name


def _normalize_color(value: str | None) -> str:
    color = (value or "").strip().lower()
    return color if color in _ALLOWED_COLORS else "slate"


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:80] if slug else "label"


def list_managed_labels(db: Session, *, include_inactive: bool = True) -> list[dict]:
    query = db.query(ConversationLabel)
    if not include_inactive:
        query = query.filter(ConversationLabel.is_active.is_(True))
    labels = query.order_by(func.lower(ConversationLabel.name).asc()).all()

    usage_rows = (
        db.query(func.lower(ConversationTag.tag).label("tag_key"), func.count(ConversationTag.id).label("count"))
        .group_by(func.lower(ConversationTag.tag))
        .all()
    )
    usage_by_tag = {str(row.tag_key): int(row.count or 0) for row in usage_rows}

    return [
        {
            "id": str(label.id),
            "name": label.name,
            "slug": label.slug,
            "color": label.color,
            "is_active": bool(label.is_active),
            "usage_count": usage_by_tag.get(label.name.lower(), 0),
        }
        for label in labels
    ]


def create_or_reactivate_label(db: Session, *, name: str, color: str) -> LabelActionResult:
    try:
        normalized_name = _normalize_name(name)
        normalized_color = _normalize_color(color)
        existing = (
            db.query(ConversationLabel).filter(func.lower(ConversationLabel.name) == normalized_name.lower()).first()
        )
        if existing:
            existing.name = normalized_name
            existing.slug = _slugify(normalized_name)
            existing.color = normalized_color
            existing.is_active = True
            db.commit()
            return LabelActionResult(ok=True)

        db.add(
            ConversationLabel(
                name=normalized_name,
                slug=_slugify(normalized_name),
                color=normalized_color,
                is_active=True,
            )
        )
        db.commit()
        return LabelActionResult(ok=True)
    except Exception as exc:
        db.rollback()
        return LabelActionResult(ok=False, error_detail=str(exc) or "Failed to save label")


def update_label(
    db: Session,
    *,
    label_id: str,
    name: str,
    color: str,
    is_active: bool,
) -> LabelActionResult:
    try:
        label = db.get(ConversationLabel, coerce_uuid(label_id))
        if not label:
            return LabelActionResult(ok=False, error_detail="Label not found")

        normalized_name = _normalize_name(name)
        duplicate = (
            db.query(ConversationLabel)
            .filter(func.lower(ConversationLabel.name) == normalized_name.lower())
            .filter(ConversationLabel.id != label.id)
            .first()
        )
        if duplicate:
            return LabelActionResult(ok=False, error_detail="A label with that name already exists")

        label.name = normalized_name
        label.slug = _slugify(normalized_name)
        label.color = _normalize_color(color)
        label.is_active = bool(is_active)
        db.commit()
        return LabelActionResult(ok=True)
    except Exception as exc:
        db.rollback()
        return LabelActionResult(ok=False, error_detail=str(exc) or "Failed to update label")


def delete_label(db: Session, *, label_id: str) -> LabelActionResult:
    try:
        label = db.get(ConversationLabel, coerce_uuid(label_id))
        if not label:
            return LabelActionResult(ok=False, error_detail="Label not found")
        db.delete(label)
        db.commit()
        return LabelActionResult(ok=True)
    except Exception as exc:
        db.rollback()
        return LabelActionResult(ok=False, error_detail=str(exc) or "Failed to delete label")


def _build_metadata_map(db: Session, tag_names: set[str]) -> dict[str, dict[str, str]]:
    if not tag_names:
        return {}

    keys = {name.lower() for name in tag_names if name.strip()}
    if not keys:
        return {}

    rows = (
        db.query(ConversationLabel)
        .filter(ConversationLabel.is_active.is_(True))
        .filter(func.lower(ConversationLabel.name).in_(keys))
        .all()
    )
    return {
        label.name.lower(): {
            "name": label.name,
            "color": label.color,
        }
        for label in rows
    }


def enrich_formatted_conversations_with_labels(db: Session, conversations: list[dict]) -> None:
    tag_names: set[str] = set()
    for conversation in conversations:
        for value in conversation.get("tags") or []:
            if isinstance(value, str) and value.strip():
                tag_names.add(value.strip())

    metadata_map = _build_metadata_map(db, tag_names)

    for conversation in conversations:
        labels: list[dict] = []
        for value in conversation.get("tags") or []:
            if not isinstance(value, str):
                continue
            name = value.strip()
            if not name:
                continue
            metadata = metadata_map.get(name.lower())
            labels.append(
                {
                    "name": name,
                    "color": metadata.get("color", "slate") if metadata else "slate",
                    "managed": bool(metadata),
                }
            )
        conversation["labels"] = labels[:5]
