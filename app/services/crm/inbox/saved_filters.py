"""Saved inbox filters/folders backed by user_filter_preferences."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from sqlalchemy.orm import Session

from app.models.user_filter_preference import UserFilterPreference

PAGE_KEY = "admin.crm.inbox.saved_filters"
MANAGED_KEYS: tuple[str, ...] = (
    "channel",
    "status",
    "outbox_status",
    "search",
    "assignment",
    "target_id",
    "agent_id",
    "assigned_from",
    "assigned_to",
    "limit",
)


def _normalize_params(raw: Mapping[str, str | None]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key in MANAGED_KEYS:
        value = raw.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            normalized[key] = text
    return normalized


def _load_row(db: Session, person_id: uuid.UUID) -> UserFilterPreference | None:
    return (
        db.query(UserFilterPreference)
        .filter(UserFilterPreference.person_id == person_id)
        .filter(UserFilterPreference.page_key == PAGE_KEY)
        .first()
    )


def list_saved_filters(db: Session, person_id: uuid.UUID) -> list[dict]:
    row = _load_row(db, person_id)
    state = row.state if row and isinstance(row.state, dict) else {}
    raw_items = state.get("items")
    if not isinstance(raw_items, list):
        return []
    items: list[dict] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip()
        params = raw.get("params")
        if not item_id or not name or not isinstance(params, dict):
            continue
        items.append(
            {
                "id": item_id,
                "name": name,
                "params": _normalize_params({k: str(v) for k, v in params.items()}),
            }
        )
    return items


def get_saved_filter(db: Session, person_id: uuid.UUID, filter_id: str) -> dict | None:
    target = str(filter_id or "").strip()
    if not target:
        return None
    for item in list_saved_filters(db, person_id):
        if item.get("id") == target:
            return item
    return None


def save_saved_filter(db: Session, person_id: uuid.UUID, *, name: str, params: Mapping[str, str | None]) -> dict | None:
    normalized_name = str(name or "").strip()
    normalized_params = _normalize_params(params)
    if not normalized_name or not normalized_params:
        return None

    items = list_saved_filters(db, person_id)
    item = {
        "id": str(uuid.uuid4()),
        "name": normalized_name,
        "params": normalized_params,
    }
    items.append(item)
    state = {"items": items}

    row = _load_row(db, person_id)
    if row:
        row.state = state
    else:
        db.add(
            UserFilterPreference(
                person_id=person_id,
                page_key=PAGE_KEY,
                state=state,
            )
        )
    db.commit()
    return item


def delete_saved_filter(db: Session, person_id: uuid.UUID, filter_id: str) -> bool:
    target = str(filter_id or "").strip()
    if not target:
        return False
    items = [item for item in list_saved_filters(db, person_id) if item.get("id") != target]
    row = _load_row(db, person_id)
    if not row:
        return False
    if not items:
        db.delete(row)
    else:
        row.state = {"items": items}
    db.commit()
    return True


def has_managed_params(query_params: Mapping[str, str]) -> bool:
    return any(key in query_params and str(query_params.get(key) or "").strip() for key in MANAGED_KEYS)


def merge_query_with_saved_filter(query_params: Mapping[str, str], params: Mapping[str, str]) -> dict[str, str]:
    merged = {str(k): str(v) for k, v in query_params.items()}
    for key in MANAGED_KEYS:
        merged.pop(key, None)
    for key, value in params.items():
        text = str(value).strip()
        if text:
            merged[str(key)] = text
    return merged
