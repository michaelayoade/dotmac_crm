"""Branding helper for Python code without request context.

Use this when you need branding values in services, Celery tasks, or email templates
that don't have access to ``request.state.branding``.  The middleware already provides
branding for templates; this module mirrors the same logic for backend code.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.services.settings_spec import resolve_values_atomic


def get_branding(db: Session) -> dict:
    """Load branding settings from DB.

    Uses the same ``SettingsCache`` (Redis-backed) as the middleware,
    so repeated calls within the cache TTL are essentially free.
    """
    keys = [
        "company_name",
        "brand_logo_url",
        "brand_favicon_url",
        "brand_color",
        "support_email",
        "support_phone",
    ]
    try:
        values = resolve_values_atomic(db, SettingDomain.comms, keys)
    except Exception:
        values = {}

    return {
        "company_name": values.get("company_name") or "Dotmac",
        "logo_url": values.get("brand_logo_url"),
        "favicon_url": values.get("brand_favicon_url"),
        "brand_color": values.get("brand_color") or "#0f172a",
        "support_email": values.get("support_email"),
        "support_phone": values.get("support_phone"),
    }
