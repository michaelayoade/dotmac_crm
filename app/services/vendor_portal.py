import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

import app.services.auth_flow as auth_flow_service
from app.models.auth import Session as AuthSession
from app.models.auth import SessionStatus
from app.models.domain_settings import SettingDomain
from app.models.person import Person
from app.models.vendor import Vendor, VendorUser
from app.services.common import coerce_uuid
from app.services.settings_spec import resolve_value

SESSION_COOKIE_NAME = "vendor_session"
# Default values for fallback
_DEFAULT_SESSION_TTL = 86400  # 24 hours
_DEFAULT_REMEMBER_TTL = 2592000  # 30 days

# Simple in-memory session store (in production, use Redis or database)
_VENDOR_SESSIONS: dict[str, dict] = {}


def _now() -> datetime:
    return datetime.now(UTC)


def _coerce_ttl(value: object | None, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return default


def _initials(person: Person) -> str:
    first = (person.first_name or "").strip()[:1]
    last = (person.last_name or "").strip()[:1]
    initials = f"{first}{last}".upper()
    return initials or "VD"


def _get_vendor_user(db: Session, person_id: str) -> VendorUser | None:
    return (
        db.query(VendorUser)
        .filter(VendorUser.person_id == coerce_uuid(person_id))
        .filter(VendorUser.is_active.is_(True))
        .order_by(VendorUser.created_at.desc())
        .first()
    )


def get_vendor_user(db: Session, person_id: str) -> VendorUser | None:
    return _get_vendor_user(db, person_id)


def _create_session(
    username: str,
    person_id: str,
    vendor_id: str,
    role: str | None,
    remember: bool,
    db: Session | None = None,
) -> str:
    session_token = secrets.token_urlsafe(32)
    ttl_seconds = _session_ttl_seconds(remember, db)
    _VENDOR_SESSIONS[session_token] = {
        "username": username,
        "person_id": person_id,
        "vendor_id": vendor_id,
        "role": role,
        "remember": remember,
        "created_at": _now().isoformat(),
        "expires_at": (_now() + timedelta(seconds=ttl_seconds)).isoformat(),
    }
    return session_token


def _get_session(session_token: str) -> dict | None:
    session = _VENDOR_SESSIONS.get(session_token)
    if not session:
        return None
    expires_at = datetime.fromisoformat(session["expires_at"])
    if _now() > expires_at:
        del _VENDOR_SESSIONS[session_token]
        return None
    return session


def invalidate_session(session_token: str) -> None:
    _VENDOR_SESSIONS.pop(session_token, None)


def login(db: Session, username: str, password: str, request: Request, remember: bool) -> dict:
    result = auth_flow_service.auth_flow.login(db, username, password, request, None)
    if result.get("mfa_required"):
        return {"mfa_required": True, "mfa_token": result.get("mfa_token")}
    access_token = result.get("access_token")
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return _session_from_access_token(db, access_token, username, remember)


def verify_mfa(db: Session, mfa_token: str, code: str, request: Request, remember: bool) -> dict:
    result = auth_flow_service.auth_flow.mfa_verify(db, mfa_token, code, request)
    access_token = result.get("access_token")
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid verification code")
    return _session_from_access_token(db, access_token, None, remember)


def _session_from_access_token(
    db: Session,
    access_token: str,
    username: str | None,
    remember: bool,
) -> dict:
    payload = auth_flow_service.decode_access_token(db, access_token)
    person_id = payload.get("sub")
    session_id = payload.get("session_id")
    if not person_id or not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")

    auth_session = db.get(AuthSession, coerce_uuid(session_id))
    if not auth_session or auth_session.status != SessionStatus.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    if auth_session.expires_at and auth_session.expires_at <= _now():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    vendor_user = _get_vendor_user(db, str(person_id))
    if not vendor_user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Vendor access required")

    person = db.get(Person, vendor_user.person_id)
    if not person:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Person not found")

    session_token = _create_session(
        username=username or person.email,
        person_id=str(person.id),
        vendor_id=str(vendor_user.vendor_id),
        role=vendor_user.role,
        remember=remember,
        db=db,
    )
    return {"session_token": session_token, "vendor_id": str(vendor_user.vendor_id)}


def get_context(db: Session, session_token: str | None) -> dict | None:
    session = _get_session(session_token or "")
    if not session:
        return None

    person = db.get(Person, coerce_uuid(session["person_id"]))
    vendor = db.get(Vendor, coerce_uuid(session["vendor_id"]))
    if not person or not vendor:
        return None

    vendor_user = _get_vendor_user(db, str(person.id))
    if not vendor_user:
        return None

    current_user = {
        "name": person.display_name or f"{person.first_name} {person.last_name}".strip(),
        "email": person.email,
        "initials": _initials(person),
    }
    return {
        "session": session,
        "current_user": current_user,
        "person": person,
        "vendor": vendor,
        "vendor_user": vendor_user,
    }


def refresh_session(session_token: str | None, db: Session | None = None) -> dict | None:
    if not session_token:
        return None
    session = _get_session(session_token)
    if not session:
        return None
    ttl_seconds = _session_ttl_seconds(session.get("remember", False), db)
    session["expires_at"] = (_now() + timedelta(seconds=ttl_seconds)).isoformat()
    return session


def _session_ttl_seconds(remember: bool, db: Session | None = None) -> int:
    """Get session TTL in seconds, using configurable settings when db is available."""
    if remember:
        ttl = resolve_value(db, SettingDomain.auth, "vendor_remember_ttl_seconds") if db else None
        return _coerce_ttl(ttl, _DEFAULT_REMEMBER_TTL)
    ttl = resolve_value(db, SettingDomain.auth, "vendor_session_ttl_seconds") if db else None
    return _coerce_ttl(ttl, _DEFAULT_SESSION_TTL)


def get_session_max_age(db: Session | None = None) -> int:
    """Get the session max age for non-remember sessions."""
    return _session_ttl_seconds(remember=False, db=db)


def get_remember_max_age(db: Session | None = None) -> int:
    """Get the session max age for remember-me sessions."""
    return _session_ttl_seconds(remember=True, db=db)
