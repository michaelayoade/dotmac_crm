"""Auth helpers for admin web routes.

This module exists to avoid circular imports between __init__.py and route modules.
"""
from fastapi import Request
from sqlalchemy.orm import Session

from app.services import web_admin as web_admin_service


def get_current_user(request: Request) -> dict:
    """Get current user from session/request."""
    return web_admin_service.get_current_user(request)


def get_sidebar_stats(db: Session) -> dict:
    """Get stats for sidebar badges."""
    return web_admin_service.get_sidebar_stats(db)
