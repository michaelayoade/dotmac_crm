import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models.auth import Session as AuthSession, SessionStatus
from app.models.domain_settings import SettingDomain
from app.models.person import Person
from app.models.subscriber import Reseller, ResellerUser
from app.services import customer_portal
import app.services.auth_flow as auth_flow_service
from app.services.common import coerce_uuid
from app.services.settings_spec import resolve_value

SESSION_COOKIE_NAME = "reseller_session"
# Default values for fallback
_DEFAULT_SESSION_TTL = 86400  # 24 hours
_DEFAULT_REMEMBER_TTL = 2592000  # 30 days

# Simple in-memory session store (in production, use Redis or database)
_RESELLER_SESSIONS: dict[str, dict] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
    return initials or "RS"


def _get_reseller_user(db: Session, person_id: str) -> ResellerUser | None:
    return (
        db.query(ResellerUser)
        .filter(ResellerUser.person_id == coerce_uuid(person_id))
        .filter(ResellerUser.is_active.is_(True))
        .order_by(ResellerUser.created_at.desc())
        .first()
    )


def _create_session(
    username: str,
    person_id: str,
    reseller_id: str,
    remember: bool,
    db: Session | None = None,
) -> str:
    session_token = secrets.token_urlsafe(32)
    ttl_seconds = _session_ttl_seconds(remember, db)
    _RESELLER_SESSIONS[session_token] = {
        "username": username,
        "person_id": person_id,
        "reseller_id": reseller_id,
        "remember": remember,
        "created_at": _now().isoformat(),
        "expires_at": (_now() + timedelta(seconds=ttl_seconds)).isoformat(),
    }
    return session_token


def _get_session(session_token: str) -> dict | None:
    session = _RESELLER_SESSIONS.get(session_token)
    if not session:
        return None
    expires_at = datetime.fromisoformat(session["expires_at"])
    if _now() > expires_at:
        del _RESELLER_SESSIONS[session_token]
        return None
    return session


def invalidate_session(session_token: str) -> None:
    _RESELLER_SESSIONS.pop(session_token, None)


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

    reseller_user = _get_reseller_user(db, str(person_id))
    if not reseller_user:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Reseller access required")

    person = db.get(Person, reseller_user.person_id)
    if not person:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Person not found")

    session_token = _create_session(
        username=username or person.email,
        person_id=str(person.id),
        reseller_id=str(reseller_user.reseller_id),
        remember=remember,
        db=db,
    )
    return {"session_token": session_token, "reseller_id": str(reseller_user.reseller_id)}


def get_context(db: Session, session_token: str | None) -> dict | None:
    session = _get_session(session_token or "")
    if not session:
        return None

    person = db.get(Person, coerce_uuid(session["person_id"]))
    reseller = db.get(Reseller, coerce_uuid(session["reseller_id"]))
    if not person or not reseller:
        return None

    reseller_user = _get_reseller_user(db, str(person.id))
    if not reseller_user:
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
        "reseller": reseller,
        "reseller_user": reseller_user,
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
        ttl = resolve_value(db, SettingDomain.auth, "reseller_remember_ttl_seconds") if db else None
        return _coerce_ttl(ttl, _DEFAULT_REMEMBER_TTL)
    ttl = resolve_value(db, SettingDomain.auth, "reseller_session_ttl_seconds") if db else None
    return _coerce_ttl(ttl, _DEFAULT_SESSION_TTL)


def get_session_max_age(db: Session | None = None) -> int:
    """Get the session max age for non-remember sessions."""
    return _session_ttl_seconds(remember=False, db=db)


def get_remember_max_age(db: Session | None = None) -> int:
    """Get the session max age for remember-me sessions."""
    return _session_ttl_seconds(remember=True, db=db)


def get_dashboard_summary(
    db: Session,
    reseller_id: str,
    limit: int,
    offset: int,
) -> dict:
    """Get dashboard summary for reseller portal."""
    # Since billing functionality was removed, return minimal dashboard info
    return {
        "reseller_id": reseller_id,
        "totals": {
            "customers": 0,  # Would need to track reseller-associated customers
        },
    }


def list_accounts(
    db: Session,
    reseller_id: str,
    limit: int,
    offset: int,
) -> list[Person]:
    """List reseller-associated customer accounts (placeholder)."""
    _ = reseller_id
    return (
        db.query(Person)
        .order_by(Person.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )


def create_customer_impersonation_session(
    db: Session,
    reseller_id: str,
    account_id: str,
    return_to: str | None = None,
) -> str:
    """Create a customer session for reseller impersonation."""
    _ = reseller_id
    person = db.get(Person, coerce_uuid(account_id))
    if not person:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return customer_portal.create_customer_session(
        username=person.email,
        person_id=person.id,
        return_to=return_to,
        remember=False,
        db=db,
    )
