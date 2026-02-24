"""RBAC helpers for server-rendered (cookie-auth) web routes."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.rbac import PersonRole, Role
from app.web.auth.dependencies import require_web_auth


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_web_role(role_name: str):
    """Require a DB-backed role for web routes.

    We do a DB check (not just JWT claims) so demotions take effect immediately.
    """

    def _dep(
        request: Request,
        auth: dict = Depends(require_web_auth),
        db: Session = Depends(_get_db),
    ) -> dict:
        roles = set(auth.get("roles") or [])
        if role_name in roles:
            return auth

        person = auth.get("person")
        if not person:
            raise HTTPException(status_code=403, detail="Forbidden")

        role = db.query(Role).filter(Role.name == role_name).filter(Role.is_active.is_(True)).first()
        if not role:
            raise HTTPException(status_code=403, detail="Role not found")

        link = (
            db.query(PersonRole).filter(PersonRole.person_id == person.id).filter(PersonRole.role_id == role.id).first()
        )
        if not link:
            raise HTTPException(status_code=403, detail="Forbidden")

        # Keep request.state.auth consistent (templates read roles).
        request.state.auth = {**auth, "roles": sorted(list(roles | {role_name}))}
        return request.state.auth

    return _dep
