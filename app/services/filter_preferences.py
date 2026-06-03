from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.user_filter_preference import UserFilterPreference


@dataclass(frozen=True)
class FilterPreferencePage:
    key: str
    managed_keys: tuple[str, ...]
    default_values: Mapping[str, str] = field(default_factory=dict)


TICKETS_PAGE = FilterPreferencePage(
    key="admin.support.tickets.list",
    managed_keys=(
        "search",
        "status",
        "ticket_type",
        "assigned",
        "region",
        "group",
        "date_from",
        "date_to",
        "subscriber",
        "filters",
        "order_by",
        "order_dir",
        "per_page",
    ),
)

PROJECTS_PAGE = FilterPreferencePage(
    key="admin.projects.list",
    managed_keys=(
        "search",
        "status",
        "project_type",
        "region",
        "date_from",
        "date_to",
        "filters",
        "order_by",
        "order_dir",
        "per_page",
    ),
    default_values={
        "order_by": "created_at",
        "order_dir": "desc",
        "per_page": "25",
    },
)

PROJECT_TASKS_PAGE = FilterPreferencePage(
    key="admin.projects.tasks.list",
    managed_keys=(
        "project_id",
        "status",
        "priority",
        "assigned",
        "filters",
        "per_page",
    ),
)


PAGES_BY_KEY = {
    TICKETS_PAGE.key: TICKETS_PAGE,
    PROJECTS_PAGE.key: PROJECTS_PAGE,
    PROJECT_TASKS_PAGE.key: PROJECT_TASKS_PAGE,
}


def _is_default_value(page: FilterPreferencePage, key: str, value: str) -> bool:
    default_value = page.default_values.get(key)
    return default_value is not None and value == default_value


def _normalize_state_for_page(page: FilterPreferencePage, state: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    managed = set(page.managed_keys)
    for key, value in state.items():
        key_text = str(key)
        if key_text not in managed:
            continue
        if value is None:
            continue
        value_text = str(value).strip()
        if not value_text or _is_default_value(page, key_text, value_text):
            continue
        normalized[key_text] = value_text
    return normalized


def get_preference(db: Session, person_id: uuid.UUID, page_key: str) -> dict[str, str] | None:
    row = (
        db.query(UserFilterPreference)
        .filter(UserFilterPreference.person_id == person_id)
        .filter(UserFilterPreference.page_key == page_key)
        .first()
    )
    if not row or not isinstance(row.state, dict):
        return None
    page = PAGES_BY_KEY.get(page_key)
    state = {str(k): str(v) for k, v in row.state.items() if v is not None and str(v).strip()}
    if page:
        state = _normalize_state_for_page(page, state)
    return state or None


def save_preference(db: Session, person_id: uuid.UUID, page_key: str, state: Mapping[str, str]) -> None:
    page = PAGES_BY_KEY.get(page_key)
    if page:
        normalized = _normalize_state_for_page(page, state)
    else:
        normalized = {str(k): str(v) for k, v in state.items() if v is not None and str(v).strip()}
    if not normalized:
        clear_preference(db, person_id, page_key)
        return

    row = (
        db.query(UserFilterPreference)
        .filter(UserFilterPreference.person_id == person_id)
        .filter(UserFilterPreference.page_key == page_key)
        .first()
    )
    if row:
        row.state = normalized
    else:
        db.add(
            UserFilterPreference(
                person_id=person_id,
                page_key=page_key,
                state=normalized,
            )
        )
    db.commit()


def clear_preference(db: Session, person_id: uuid.UUID, page_key: str) -> None:
    (
        db.query(UserFilterPreference)
        .filter(UserFilterPreference.person_id == person_id)
        .filter(UserFilterPreference.page_key == page_key)
        .delete(synchronize_session=False)
    )
    db.commit()


def has_managed_params(query_params: Mapping[str, str], page: FilterPreferencePage) -> bool:
    return any(key in query_params for key in page.managed_keys)


def extract_managed_state(query_params: Mapping[str, str], page: FilterPreferencePage) -> dict[str, str]:
    state: dict[str, str] = {}
    for key in page.managed_keys:
        value = query_params.get(key)
        if value is None:
            continue
        value = value.strip()
        if not value or _is_default_value(page, key, value):
            continue
        state[key] = value
    return state


def remove_default_query_values(query_params: Mapping[str, str], page: FilterPreferencePage) -> dict[str, str]:
    normalized: dict[str, str] = {}
    managed = set(page.managed_keys)
    for key, value in query_params.items():
        key_text = str(key)
        if value is None:
            continue
        value_text = str(value).strip()
        if key_text in managed and (not value_text or _is_default_value(page, key_text, value_text)):
            continue
        normalized[key_text] = value_text
    return normalized


def merge_query_with_state(
    query_params: Mapping[str, str],
    page: FilterPreferencePage,
    state: Mapping[str, str],
) -> dict[str, str]:
    merged = {str(k): str(v) for k, v in query_params.items()}
    for key in page.managed_keys:
        merged.pop(key, None)
    managed = set(page.managed_keys)
    for key, value in state.items():
        if str(key) not in managed:
            continue
        if value is None:
            continue
        text = str(value).strip()
        if text and not _is_default_value(page, str(key), text):
            merged[str(key)] = text
    return merged
