"""Permission helpers for CRM campaigns workflows."""

from __future__ import annotations

from collections.abc import Iterable


def _normalize(values: Iterable[str] | None) -> set[str]:
    if not values:
        return set()
    return {str(value).strip() for value in values if value}


def is_admin(roles: Iterable[str] | None = None, scopes: Iterable[str] | None = None) -> bool:
    role_set = _normalize(roles)
    if "admin" in role_set:
        return True
    scope_set = _normalize(scopes)
    return bool({"crm:campaign:admin", "crm:campaign:*"} & scope_set)


def can_view_campaigns(roles: Iterable[str] | None = None, scopes: Iterable[str] | None = None) -> bool:
    if is_admin(roles, scopes):
        return True
    scope_set = _normalize(scopes)
    return bool(
        {
            "crm:campaign:read",
            "crm:campaign:write",
            "crm:campaign:*",
            "crm:campaign",
            "crm",
        }
        & scope_set
    )


def can_write_campaigns(roles: Iterable[str] | None = None, scopes: Iterable[str] | None = None) -> bool:
    if is_admin(roles, scopes):
        return True
    scope_set = _normalize(scopes)
    return bool(
        {
            "crm:campaign:write",
            "crm:campaign:*",
            "crm:campaign",
            "crm",
        }
        & scope_set
    )
