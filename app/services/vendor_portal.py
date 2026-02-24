from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request, status
from jose import JWTError, jwt
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


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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


def _vendor_is_active(vendor: Vendor | None) -> bool:
    return bool(vendor and getattr(vendor, "is_active", False))


def _person_is_active(person: Person | None) -> bool:
    if not person:
        return False
    if not getattr(person, "is_active", False):
        return False
    # Be strict: inactive/archived people should not access the vendor portal.
    status = getattr(person, "status", None)
    status_value = getattr(status, "value", status)
    return str(status_value or "").lower() == "active"


def _create_session(
    username: str,
    person_id: str,
    vendor_id: str,
    role: str | None,
    remember: bool,
    session_id: str | None = None,
    db: Session | None = None,
) -> str:
    ttl_seconds = _session_ttl_seconds(remember, db)
    now = _now()
    expires_at = now + timedelta(seconds=ttl_seconds)
    session_payload = {
        "typ": "vendor_portal",
        "username": username,
        "person_id": person_id,
        "vendor_id": vendor_id,
        "role": role,
        "remember": remember,
        "session_id": session_id,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    session_token = jwt.encode(
        session_payload,
        auth_flow_service._jwt_secret(db),
        algorithm=auth_flow_service._jwt_algorithm(db),
    )
    # Keep in-memory compatibility for any code paths still relying on local state.
    _VENDOR_SESSIONS[session_token] = session_payload
    return session_token


def _get_session(session_token: str, db: Session | None = None) -> dict | None:
    if not session_token:
        return None

    # Preferred path: stateless signed token, valid across workers.
    try:
        payload = jwt.decode(
            session_token,
            auth_flow_service._jwt_secret(db),
            algorithms=[auth_flow_service._jwt_algorithm(db)],
        )
        if payload.get("typ") == "vendor_portal":
            return {
                "username": str(payload.get("username") or ""),
                "person_id": str(payload.get("person_id") or ""),
                "vendor_id": str(payload.get("vendor_id") or ""),
                "role": payload.get("role"),
                "remember": bool(payload.get("remember", False)),
                "session_id": str(payload.get("session_id") or "") or None,
                "created_at": str(payload.get("created_at") or ""),
                "expires_at": str(payload.get("expires_at") or ""),
            }
    except JWTError:
        pass

    # Fallback path for legacy in-memory session tokens.
    session = _VENDOR_SESSIONS.get(session_token)
    if not session:
        return None
    expires_at = _as_utc(datetime.fromisoformat(session["expires_at"]))
    if expires_at and _now() > expires_at:
        del _VENDOR_SESSIONS[session_token]
        return None
    return session


def invalidate_session(session_token: str, db: Session | None = None) -> None:
    """Invalidate a vendor session token.

    Removes from in-memory cache and, if a db session is provided,
    revokes the underlying AuthSession to prevent reuse across workers.
    """
    _VENDOR_SESSIONS.pop(session_token, None)
    if db and session_token:
        try:
            payload = jwt.decode(
                session_token,
                auth_flow_service._jwt_secret(db),
                algorithms=[auth_flow_service._jwt_algorithm(db)],
            )
            sid = payload.get("session_id")
            if sid:
                auth_session = db.get(AuthSession, coerce_uuid(sid))
                if auth_session and auth_session.status == SessionStatus.active:
                    auth_session.status = SessionStatus.revoked
                    db.commit()
        except JWTError:
            pass


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
    auth_expires_at = _as_utc(auth_session.expires_at)
    if auth_expires_at and auth_expires_at <= _now():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    vendor_user = _get_vendor_user(db, str(person_id))
    if not vendor_user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Vendor access required")

    person = db.get(Person, vendor_user.person_id)
    if not person:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Person not found")
    if not _person_is_active(person):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Person is inactive")

    vendor = db.get(Vendor, vendor_user.vendor_id)
    if not _vendor_is_active(vendor):
        # Vendor record can be deactivated independently of vendor_user links.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Vendor is inactive")

    session_token = _create_session(
        username=username or person.email,
        person_id=str(person.id),
        vendor_id=str(vendor_user.vendor_id),
        role=vendor_user.role,
        remember=remember,
        session_id=str(session_id),
        db=db,
    )
    return {"session_token": session_token, "vendor_id": str(vendor_user.vendor_id)}


def get_context(db: Session, session_token: str | None) -> dict | None:
    session = _get_session(session_token or "", db)
    if not session:
        return None

    auth_session_id = session.get("session_id")
    if auth_session_id:
        auth_session = db.get(AuthSession, coerce_uuid(auth_session_id))
        if not auth_session or auth_session.status != SessionStatus.active:
            invalidate_session(session_token or "", db=db)
            return None
        auth_expires_at = _as_utc(auth_session.expires_at)
        if auth_expires_at and auth_expires_at <= _now():
            invalidate_session(session_token or "", db=db)
            return None

    person = db.get(Person, coerce_uuid(session["person_id"]))
    vendor = db.get(Vendor, coerce_uuid(session["vendor_id"]))
    if not person or not vendor or not _person_is_active(person) or not _vendor_is_active(vendor):
        # Session is no longer valid if either side is deactivated.
        invalidate_session(session_token or "", db=db)
        return None

    vendor_user = _get_vendor_user(db, str(person.id))
    if not vendor_user:
        invalidate_session(session_token or "", db=db)
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
    session = _get_session(session_token, db)
    if not session:
        return None
    renewed_token = _create_session(
        username=str(session.get("username") or ""),
        person_id=str(session.get("person_id") or ""),
        vendor_id=str(session.get("vendor_id") or ""),
        role=session.get("role"),
        remember=bool(session.get("remember", False)),
        session_id=str(session.get("session_id") or "") or None,
        db=db,
    )
    refreshed = _get_session(renewed_token, db) or session
    refreshed["session_token"] = renewed_token
    return refreshed


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
