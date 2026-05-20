"""Web authentication dependencies for cookie-based auth with redirects."""

import logging
from datetime import UTC, datetime
from time import monotonic
from urllib.parse import quote

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth_exceptions import AuthenticationRequired
from app.db import end_read_only_transaction, get_request_db_session
from app.models.auth import Session as AuthSession
from app.models.auth import SessionStatus
from app.models.person import Person
from app.services.auth_flow import _load_rbac_claims, decode_access_token

logger = logging.getLogger(__name__)
_AUTH_LOG_THRESHOLD_MS = 250.0


def _get_db(request: Request):
    yield from get_request_db_session(request)


def get_session_token(request: Request) -> str | None:
    """Extract session token from cookie or Authorization header."""
    # First check for cookie-based token
    cookie_token = request.cookies.get("session_token")
    if cookie_token:
        return cookie_token

    # Fall back to Bearer token from Authorization header (for API calls)
    auth_header = request.headers.get("authorization")
    if auth_header:
        parts = auth_header.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()

    return None


def validate_session_token(
    request: Request,
    db: Session = Depends(_get_db),
) -> dict | None:
    """Validate session token and return user info if valid.

    Returns None if not authenticated (doesn't raise).
    """
    token = get_session_token(request)
    if not token:
        return None

    start = monotonic()
    outcome = "unknown"
    try:
        payload = decode_access_token(db, token)
    except Exception:
        outcome = "decode_failed"
        end_read_only_transaction(db)
        _log_auth_timing(request, outcome=outcome, duration_ms=(monotonic() - start) * 1000.0)
        return None

    person_id = payload.get("sub")
    session_id = payload.get("session_id")
    if not person_id or not session_id:
        outcome = "claims_missing"
        _log_auth_timing(request, outcome=outcome, duration_ms=(monotonic() - start) * 1000.0)
        return None

    now = datetime.now(UTC)
    session = (
        db.query(AuthSession)
        .filter(AuthSession.id == session_id)
        .filter(AuthSession.person_id == person_id)
        .filter(AuthSession.status == SessionStatus.active)
        .filter(AuthSession.revoked_at.is_(None))
        .filter(AuthSession.expires_at > now)
        .first()
    )
    if not session:
        outcome = "session_missing"
        end_read_only_transaction(db)
        _log_auth_timing(request, outcome=outcome, duration_ms=(monotonic() - start) * 1000.0)
        return None

    # Get person details
    person = db.get(Person, person_id)
    if not person:
        outcome = "person_missing"
        end_read_only_transaction(db)
        _log_auth_timing(request, outcome=outcome, duration_ms=(monotonic() - start) * 1000.0)
        return None

    roles, scopes = _load_rbac_claims(db, str(person_id))
    outcome = "authenticated"
    end_read_only_transaction(db)
    _log_auth_timing(request, outcome=outcome, duration_ms=(monotonic() - start) * 1000.0)

    return {
        "person_id": str(person_id),
        "session_id": str(session_id),
        "roles": roles if isinstance(roles, list) else [],
        "scopes": scopes if isinstance(scopes, list) else [],
        "person": person,
    }


def _log_auth_timing(request: Request, *, outcome: str, duration_ms: float) -> None:
    if duration_ms < _AUTH_LOG_THRESHOLD_MS:
        return
    logger.info(
        "web_auth_validate_session_slow path=%s outcome=%s duration_ms=%.2f request_id=%s",
        request.url.path,
        outcome,
        duration_ms,
        getattr(request.state, "request_id", None),
    )


def require_web_auth(
    request: Request,
    db: Session = Depends(_get_db),
) -> dict:
    """Require authentication for web routes.

    Raises AuthenticationRequired if not authenticated.
    The exception handler should redirect to login with next URL.
    """
    auth_info = validate_session_token(request, db)
    if not auth_info:
        # Build redirect URL with next parameter
        next_url = str(request.url.path)
        if request.url.query:
            next_url += f"?{request.url.query}"
        redirect_url = f"/auth/refresh?next={quote(next_url)}"
        raise AuthenticationRequired(redirect_url)

    # Store auth info in request state for use by templates
    request.state.auth = auth_info
    request.state.user = auth_info["person"]
    request.state.actor_id = auth_info["person_id"]
    request.state.actor_type = "user"

    return auth_info


def get_current_user_from_auth(auth: dict = Depends(require_web_auth)) -> dict:
    """Get current user info formatted for templates."""
    person = auth.get("person")
    if not person:
        return {
            "id": "",
            "initials": "??",
            "name": "Unknown User",
            "email": "",
        }

    name = f"{person.first_name} {person.last_name}".strip()
    initials = _get_initials(name)

    return {
        "id": str(person.id),
        "initials": initials,
        "name": name,
        "email": person.email or "",
        "roles": auth.get("roles", []),
    }


def _get_initials(name: str) -> str:
    """Get initials from a name."""
    if not name:
        return "??"
    parts = name.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return name[0:2].upper()
