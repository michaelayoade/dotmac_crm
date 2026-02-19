from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock
from time import monotonic

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.services import settings_spec

# Branding rarely changes; cache it to avoid DB work on every request.
_BRANDING_CACHE: dict | None = None
_BRANDING_CACHE_AT: float | None = None
_BRANDING_CACHE_TTL_SECONDS = 300.0  # 5 minutes
_BRANDING_LOCK = Lock()


def invalidate_branding_cache() -> None:
    global _BRANDING_CACHE, _BRANDING_CACHE_AT
    with _BRANDING_LOCK:
        _BRANDING_CACHE = None
        _BRANDING_CACHE_AT = None


def load_branding_settings(db: Session, *, force: bool = False) -> dict:
    """Load branding settings with a small in-memory TTL cache.

    Use ``force=True`` after updating branding settings so the caller sees the new values immediately.
    """
    global _BRANDING_CACHE, _BRANDING_CACHE_AT
    now = monotonic()

    if not force:
        cache = _BRANDING_CACHE
        cache_at = _BRANDING_CACHE_AT
        if cache is not None and cache_at is not None and now - cache_at < _BRANDING_CACHE_TTL_SECONDS:
            return cache

    with _BRANDING_LOCK:
        if (
            not force
            and _BRANDING_CACHE is not None
            and _BRANDING_CACHE_AT is not None
            and now - _BRANDING_CACHE_AT < _BRANDING_CACHE_TTL_SECONDS
        ):
            return _BRANDING_CACHE

        try:
            branding_keys = [
                "company_name",
                "brand_logo_url",
                "brand_favicon_url",
                "brand_color",
                "support_email",
                "support_phone",
            ]
            values = settings_spec.resolve_values_atomic(db, SettingDomain.comms, branding_keys)
            result = {
                "company_name": values.get("company_name") or "Dotmac",
                "logo_url": values.get("brand_logo_url"),
                "favicon_url": values.get("brand_favicon_url"),
                "brand_color": values.get("brand_color") or "#0f172a",
                "support_email": values.get("support_email"),
                "support_phone": values.get("support_phone"),
                "current_year": datetime.now(UTC).year,
            }
        except Exception:
            result = {
                "company_name": "Dotmac",
                "logo_url": None,
                "favicon_url": None,
                "brand_color": "#0f172a",
                "support_email": None,
                "support_phone": None,
                "current_year": datetime.now(UTC).year,
            }

        _BRANDING_CACHE = result
        _BRANDING_CACHE_AT = now
        return result
