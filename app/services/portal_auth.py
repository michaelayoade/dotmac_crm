"""Customer Portal authentication dependency.

The Portal API (``/api/v1/portal/*``) is consumed directly by the customer app
(and the reseller portal) using a short-lived, subject-scoped **portal token** —
a ``typ=portal`` JWT minted by the sub backend via ``/portal/internal/session``
(see ``app.api.crm.portal``). This dependency verifies that token and yields the
scoped principal; every Portal route must depend on it so the subject scope is
enforced server-side (a client can never widen its own scope).
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.auth_flow import decode_portal_token


class PortalPrincipal:
    """The scoped subject behind a portal token."""

    def __init__(self, subject_id: str, actor: str, scopes: list[str] | None):
        self.subject_id = subject_id
        # "subscriber" → scope to one subscriber; "reseller" → scope to the
        # reseller org subtree (enforced by the data routes per RFC #73).
        self.actor = actor
        self.scopes = set(scopes or [])

    def require_scope(self, scope: str) -> None:
        if scope not in self.scopes:
            raise HTTPException(status_code=403, detail=f"Missing scope: {scope}")


def require_portal_auth(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> PortalPrincipal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split(" ", 1)[1].strip()
    claims = decode_portal_token(db, token)  # raises 401 on invalid/expired/wrong typ
    subject_id = claims.get("sub")
    if not subject_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    return PortalPrincipal(
        subject_id=str(subject_id),
        actor=str(claims.get("actor") or "subscriber"),
        scopes=list(claims.get("scopes") or []),
    )
