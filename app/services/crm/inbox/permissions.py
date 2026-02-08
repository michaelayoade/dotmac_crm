"""Permission helpers for CRM inbox workflows."""

from __future__ import annotations

from typing import Iterable


def _normalize(values: Iterable[str] | None) -> set[str]:
    if not values:
        return set()
    return {str(value).strip() for value in values if value}


def is_admin(roles: Iterable[str] | None = None, scopes: Iterable[str] | None = None) -> bool:
    role_set = _normalize(roles)
    if "admin" in role_set:
        return True
    scope_set = _normalize(scopes)
    return bool({"crm:inbox:admin", "crm:inbox:*"} & scope_set)


def can_view_inbox_settings(
    roles: Iterable[str] | None = None, scopes: Iterable[str] | None = None
) -> bool:
    if is_admin(roles, scopes):
        return True
    scope_set = _normalize(scopes)
    return bool(
        {
            "crm:inbox:settings:read",
            "crm:inbox:settings:write",
            "crm:inbox:settings:*",
            "crm:inbox:*",
        }
        & scope_set
    )


def can_manage_inbox_settings(
    roles: Iterable[str] | None = None, scopes: Iterable[str] | None = None
) -> bool:
    if is_admin(roles, scopes):
        return True
    scope_set = _normalize(scopes)
    return bool(
        {
            "crm:inbox:settings:write",
            "crm:inbox:settings:*",
            "crm:inbox:*",
        }
        & scope_set
    )


def can_view_private_note(
    *,
    visibility: str | None,
    author_id: str | None,
    actor_id: str | None,
    roles: Iterable[str] | None = None,
    scopes: Iterable[str] | None = None,
) -> bool:
    vis = (visibility or "team").strip().lower()
    if vis == "author":
        if actor_id and author_id and actor_id == author_id:
            return True
        return is_admin(roles, scopes)
    if vis == "admins":
        return is_admin(roles, scopes)
    return True
