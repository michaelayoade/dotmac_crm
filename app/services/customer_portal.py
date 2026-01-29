import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.person import Person
from app.models.tickets import Ticket, TicketPriority, TicketStatus
from app.services import tickets as tickets_service
from app.services.settings_spec import resolve_value

SESSION_COOKIE_NAME = "customer_session"
# Default values for fallback when db is not available
_DEFAULT_SESSION_TTL = 86400  # 24 hours
_DEFAULT_REMEMBER_TTL = 2592000  # 30 days

# Simple in-memory session store (in production, use Redis or database)
_CUSTOMER_SESSIONS: dict[str, dict] = {}


def create_customer_session(
    username: str,
    person_id: Optional[UUID] = None,
    return_to: Optional[str] = None,
    remember: bool = False,
    db: Session | None = None,
) -> str:
    """Create a new customer session and return the session token."""
    session_token = secrets.token_urlsafe(32)
    ttl_seconds = _session_ttl_seconds(remember, db)
    _CUSTOMER_SESSIONS[session_token] = {
        "username": username,
        "person_id": str(person_id) if person_id else None,
        "return_to": return_to,
        "remember": remember,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat(),
    }
    return session_token


def get_customer_session(session_token: str) -> Optional[dict]:
    """Get customer session data from token."""
    session = _CUSTOMER_SESSIONS.get(session_token)
    if not session:
        return None

    # Check expiration
    expires_at = datetime.fromisoformat(session["expires_at"])
    if datetime.now(timezone.utc) > expires_at:
        del _CUSTOMER_SESSIONS[session_token]
        return None

    return session


def refresh_customer_session(session_token: str, db: Session | None = None) -> Optional[dict]:
    session = _CUSTOMER_SESSIONS.get(session_token)
    if not session:
        return None

    expires_at = datetime.fromisoformat(session["expires_at"])
    if datetime.now(timezone.utc) > expires_at:
        del _CUSTOMER_SESSIONS[session_token]
        return None

    ttl_seconds = _session_ttl_seconds(session.get("remember", False), db)
    session["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
    return session


def invalidate_customer_session(session_token: str) -> None:
    """Invalidate a customer session."""
    _CUSTOMER_SESSIONS.pop(session_token, None)


def _coerce_int(value: object | None, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _session_ttl_seconds(remember: bool, db: Session | None = None) -> int:
    """Get session TTL in seconds, using configurable settings when db is available."""
    if remember:
        ttl = resolve_value(db, SettingDomain.auth, "customer_remember_ttl_seconds") if db else None
        return _coerce_int(ttl, _DEFAULT_REMEMBER_TTL)
    else:
        ttl = resolve_value(db, SettingDomain.auth, "customer_session_ttl_seconds") if db else None
        return _coerce_int(ttl, _DEFAULT_SESSION_TTL)


def get_session_max_age(db: Session | None = None) -> int:
    """Get the session max age for non-remember sessions."""
    return _session_ttl_seconds(remember=False, db=db)


def get_remember_max_age(db: Session | None = None) -> int:
    """Get the session max age for remember-me sessions."""
    return _session_ttl_seconds(remember=True, db=db)


def get_customer_tickets(db: Session, person_id: str, limit: int = 10) -> list:
    """Get recent tickets for a customer."""
    return tickets_service.tickets.list(
        db=db,
        subscriber_id=None,
        status=None,
        priority=None,
        channel=None,
        search=None,
        created_by_person_id=person_id,
        assigned_to_person_id=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=limit,
        offset=0,
    )


def get_customer_context(db: Session, session_token: str | None) -> dict | None:
    """Get the customer context for templates."""
    session = get_customer_session(session_token or "")
    if not session:
        return None

    person_id = session.get("person_id")
    if not person_id:
        return None

    from app.services.common import coerce_uuid
    person = db.get(Person, coerce_uuid(person_id))
    if not person:
        return None

    return {
        "session": session,
        "person": person,
        "current_user": {
            "name": person.display_name or f"{person.first_name} {person.last_name}".strip(),
            "email": person.email,
        },
    }
