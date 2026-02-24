"""Agent helpers for CRM inbox."""

from __future__ import annotations

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models.auth import UserCredential
from app.models.crm.team import CrmAgent
from app.models.person import Person
from app.models.service_team import ServiceTeam, ServiceTeamMember
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

    Shape:
    - {"id": "<agent_uuid>", "label": "<display name>", "kind": "agent"}
    - {"id": "group:<team_uuid>", "label": "<group name>", "kind": "group"}
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
    items = [{"id": str(agent.id), "label": labels.get(str(agent.id), "Agent"), "kind": "agent"} for agent in agents]

    group_rows = (
        db.query(ServiceTeam.id, ServiceTeam.name, func.count(ServiceTeamMember.person_id).label("member_count"))
        .join(
            ServiceTeamMember,
            and_(
                ServiceTeamMember.team_id == ServiceTeam.id,
                ServiceTeamMember.is_active.is_(True),
            ),
        )
        .join(
            Person,
            and_(
                Person.id == ServiceTeamMember.person_id,
                Person.is_active.is_(True),
            ),
        )
        .join(
            UserCredential,
            and_(
                UserCredential.person_id == Person.id,
                UserCredential.is_active.is_(True),
            ),
        )
        .filter(ServiceTeam.is_active.is_(True))
        .group_by(ServiceTeam.id, ServiceTeam.name)
        .order_by(ServiceTeam.name.asc())
        .limit(safe_limit)
        .all()
    )
    for team_id, team_name, member_count in group_rows:
        label = ((team_name or "Group").strip() or "Group") + f" (Group · {int(member_count or 0)})"
        items.append({"id": f"group:{team_id}", "label": label, "kind": "group"})
    items.sort(key=lambda item: (item.get("label") or "").lower())
    return items
