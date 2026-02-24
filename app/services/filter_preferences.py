from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.user_filter_preference import UserFilterPreference


@dataclass(frozen=True)
class FilterPreferencePage:
    key: str
    managed_keys: tuple[str, ...]


TICKETS_PAGE = FilterPreferencePage(
    key="admin.support.tickets.list",
    managed_keys=(
        "search",
        "status",
        "ticket_type",
        "assigned",
        "pm",
        "spc",
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
        "pm",
        "spc",
        "filters",
        "order_by",
        "order_dir",
        "per_page",
    ),
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


def get_preference(db: Session, person_id: uuid.UUID, page_key: str) -> dict[str, str] | None:
    row = (
        db.query(UserFilterPreference)
        .filter(UserFilterPreference.person_id == person_id)
        .filter(UserFilterPreference.page_key == page_key)
        .first()
    )
    if not row or not isinstance(row.state, dict):
        return None
    return {str(k): str(v) for k, v in row.state.items() if v is not None and str(v).strip()}


def save_preference(db: Session, person_id: uuid.UUID, page_key: str, state: Mapping[str, str]) -> None:
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
        if not value:
            continue
        state[key] = value
    return state


def merge_query_with_state(
    query_params: Mapping[str, str],
    page: FilterPreferencePage,
    state: Mapping[str, str],
) -> dict[str, str]:
    merged = {str(k): str(v) for k, v in query_params.items()}
    for key in page.managed_keys:
        merged.pop(key, None)
    for key, value in state.items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            merged[str(key)] = text
    return merged
