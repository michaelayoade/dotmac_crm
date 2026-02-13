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


def list_active_agents_for_mentions(db: Session, *, limit: int = 200) -> list[dict]:
    """Return active agent options suitable for @mention autocomplete.

    Shape: [{"id": "<agent_uuid>", "label": "<display name>"}]
    """
    from app.services.crm.teams.service import get_agent_labels

    safe_limit = max(int(limit or 200), 1)
    agents = (
        db.query(CrmAgent)
        .filter(CrmAgent.is_active.is_(True))
        .order_by(CrmAgent.created_at.desc())
        .limit(safe_limit)
        .all()
    )
    labels = get_agent_labels(db, agents)
    items = [{"id": str(agent.id), "label": labels.get(str(agent.id), "Agent")} for agent in agents]
    items.sort(key=lambda item: (item.get("label") or "").lower())
    return items
