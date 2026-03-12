"""Auth helpers for admin web routes.

This module exists to avoid circular imports between __init__.py and route modules.
"""

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.services import web_admin as web_admin_service

ADMIN_ROLE = "admin"
AGENT_ROLE = "Agents"
STATUS_UPDATE_ROLE_BLOCK_MESSAGE = (
    "Please reach out to Customer Experience to update the status of this service—they are the only team "
    "authorized to make this change. Thank you."
)


def get_current_user(request: Request) -> dict:
    """Get current user from session/request."""
    return web_admin_service.get_current_user(request)


def get_sidebar_stats(db: Session) -> dict:
    """Get stats for sidebar badges."""
    return web_admin_service.get_sidebar_stats(db)


def require_agent_or_admin_status_update(current_user: dict | None) -> None:
    roles = {str(role).strip() for role in (current_user or {}).get("roles", []) if str(role).strip()}
    if ADMIN_ROLE in roles or AGENT_ROLE in roles:
        return
    raise HTTPException(status_code=403, detail=STATUS_UPDATE_ROLE_BLOCK_MESSAGE)


def require_agent_or_admin_ticket_relationships(current_user: dict | None) -> None:
    roles = {str(role).strip() for role in (current_user or {}).get("roles", []) if str(role).strip()}
    if ADMIN_ROLE in roles or AGENT_ROLE in roles:
        return
    raise HTTPException(status_code=403, detail="Only Agents and Admin can merge or link tickets.")
