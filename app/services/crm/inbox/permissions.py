"""Permission helpers for CRM inbox workflows."""

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
    return bool({"crm:inbox:admin", "crm:inbox:*"} & scope_set)


def can_view_inbox_settings(roles: Iterable[str] | None = None, scopes: Iterable[str] | None = None) -> bool:
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


def can_manage_inbox_settings(roles: Iterable[str] | None = None, scopes: Iterable[str] | None = None) -> bool:
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


def can_view_inbox(roles: Iterable[str] | None = None, scopes: Iterable[str] | None = None) -> bool:
    if is_admin(roles, scopes):
        return True
    scope_set = _normalize(scopes)
    return bool(
        {
            "crm:conversation:read",
            "crm:conversation:write",
            "crm:conversation:*",
            "crm:conversation",
            "crm:inbox:read",
            "crm:inbox:write",
            "crm:inbox:*",
            "crm:inbox",
            "crm",
        }
        & scope_set
    )


def can_write_inbox(roles: Iterable[str] | None = None, scopes: Iterable[str] | None = None) -> bool:
    if is_admin(roles, scopes):
        return True
    scope_set = _normalize(scopes)
    return bool(
        {
            "crm:conversation:write",
            "crm:conversation:*",
            "crm:conversation",
            "crm:inbox:write",
            "crm:inbox:*",
            "crm:inbox",
            "crm",
        }
        & scope_set
    )


def can_send_message(roles: Iterable[str] | None = None, scopes: Iterable[str] | None = None) -> bool:
    return can_write_inbox(roles, scopes)


def can_assign_conversation(roles: Iterable[str] | None = None, scopes: Iterable[str] | None = None) -> bool:
    return can_write_inbox(roles, scopes)


def can_update_conversation_status(roles: Iterable[str] | None = None, scopes: Iterable[str] | None = None) -> bool:
    return can_write_inbox(roles, scopes)


def can_resolve_conversation(roles: Iterable[str] | None = None, scopes: Iterable[str] | None = None) -> bool:
    return can_write_inbox(roles, scopes)


def can_manage_private_notes(roles: Iterable[str] | None = None, scopes: Iterable[str] | None = None) -> bool:
    return can_write_inbox(roles, scopes)


def can_reply_to_comments(roles: Iterable[str] | None = None, scopes: Iterable[str] | None = None) -> bool:
    return can_write_inbox(roles, scopes)


def can_upload_attachments(roles: Iterable[str] | None = None, scopes: Iterable[str] | None = None) -> bool:
    return can_write_inbox(roles, scopes)


def can_use_macros(roles: Iterable[str] | None = None, scopes: Iterable[str] | None = None) -> bool:
    if is_admin(roles, scopes):
        return True
    scope_set = _normalize(scopes)
    return bool(
        {
            "crm:macro:read",
            "crm:macro:write",
            "crm:conversation:write",
            "crm:inbox:write",
            "crm:inbox:*",
            "crm:conversation:*",
        }
        & scope_set
    )


def can_manage_macros(roles: Iterable[str] | None = None, scopes: Iterable[str] | None = None) -> bool:
    if is_admin(roles, scopes):
        return True
    scope_set = _normalize(scopes)
    return bool(
        {
            "crm:macro:write",
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
