from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.services import reseller_portal as reseller_portal_service
from app.web.auth.dependencies import require_web_auth


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_reseller_portal_context(
    request: Request,
    auth: dict = Depends(require_web_auth),
    db: Session = Depends(_get_db),
) -> dict:
    person = auth["person"]
    reseller_org = reseller_portal_service.ensure_reseller_portal_access(db, person_id=person.id)
    allowed_org_ids = reseller_portal_service.get_allowed_org_ids(db, reseller_org_id=reseller_org.id)

    context = {
        **auth,
        "reseller_org": reseller_org,
        "allowed_org_ids": allowed_org_ids,
    }
    request.state.reseller_context = context
    return context
