"""Agent helpers for CRM inbox."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.crm.team import CrmAgent
from app.services.common import coerce_uuid


def get_current_agent_id(db: Session, person_id: str | None) -> str | None:
    if not person_id:
        return None
    try:
        person_uuid = coerce_uuid(person_id)
    except Exception:
        return None
    agent = db.query(CrmAgent).filter(CrmAgent.person_id == person_uuid, CrmAgent.is_active.is_(True)).first()
    return str(agent.id) if agent else None
