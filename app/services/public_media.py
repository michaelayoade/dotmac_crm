"""Signed public links for CRM message attachments."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time

from sqlalchemy.orm import Session

from app.services import email as email_service

_STORED_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def is_valid_stored_name(stored_name: str | None) -> bool:
    if not stored_name:
        return False
    return bool(_STORED_NAME_RE.fullmatch(stored_name))


def _signature_secret() -> str | None:
    return os.getenv("MEDIA_URL_SECRET") or os.getenv("JWT_SECRET") or os.getenv("SECRET_KEY")


def _sign(stored_name: str, exp: int) -> str:
    secret = _signature_secret()
    if not secret:
        raise RuntimeError("MEDIA_URL_SECRET (or JWT_SECRET/SECRET_KEY) is required for signed media URLs")
    payload = f"{stored_name}:{exp}".encode()
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def verify_media_signature(stored_name: str, exp: int, sig: str) -> bool:
    if exp < int(time.time()):
        return False
    if not _signature_secret():
        return False
    expected = _sign(stored_name, exp)
    return hmac.compare_digest(expected, sig)


def build_public_media_url(
    db: Session,
    *,
    stored_name: str,
    ttl_seconds: int = 900,
) -> str:
    """Generate an absolute signed URL for a message attachment."""
    base_url = email_service.get_app_url(db).rstrip("/")
    exp = int(time.time()) + max(60, int(ttl_seconds))
    sig = _sign(stored_name, exp)
    return f"{base_url}/public/media/messages/{stored_name}?exp={exp}&sig={sig}"
