"""Agent helpers for CRM inbox."""

from __future__ import annotations

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models.auth import UserCredential
from app.models.crm.team import CrmAgent, CrmAgentTeam, CrmTeam
from app.models.person import Person
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
        db.query(CrmTeam.id, CrmTeam.name, func.count(func.distinct(CrmAgent.person_id)).label("member_count"))
        .join(
            CrmAgentTeam,
            and_(
                CrmAgentTeam.team_id == CrmTeam.id,
                CrmAgentTeam.is_active.is_(True),
            ),
        )
        .join(
            CrmAgent,
            and_(
                CrmAgent.id == CrmAgentTeam.agent_id,
                CrmAgent.is_active.is_(True),
                CrmAgent.person_id.isnot(None),
            ),
        )
        .join(
            Person,
            and_(
                Person.id == CrmAgent.person_id,
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
        .filter(CrmTeam.is_active.is_(True))
        .group_by(CrmTeam.id, CrmTeam.name)
        .order_by(CrmTeam.name.asc())
        .limit(safe_limit)
        .all()
    )
    for team_id, team_name, member_count in group_rows:
        label = ((team_name or "Group").strip() or "Group") + f" (Group · {int(member_count or 0)})"
        items.append({"id": f"group:{team_id}", "label": label, "kind": "group"})
    items.sort(key=lambda item: (item.get("label") or "").lower())
    return items


def resolve_mentioned_person_ids_for_inbox(db: Session, mentioned_ids: list[str] | None) -> list[str]:
    """Resolve CRM inbox mention tokens to web-user person IDs."""
    if not mentioned_ids:
        return []

    agent_uuids = []
    team_uuids = []
    for raw in mentioned_ids:
        token = (raw or "").strip()
        if not token:
            continue
        if token.startswith("group:"):
            token = token.split(":", 1)[1].strip()
            try:
                team_uuids.append(coerce_uuid(token))
            except (ValueError, AttributeError):
                continue
            continue
        if token.startswith("agent:"):
            token = token.split(":", 1)[1].strip()
        try:
            agent_uuids.append(coerce_uuid(token))
        except (ValueError, AttributeError):
            continue

    recipient_person_ids: list[str] = []
    seen: set[str] = set()

    if agent_uuids:
        agents = (
            db.query(CrmAgent)
            .filter(CrmAgent.id.in_(agent_uuids))
            .filter(CrmAgent.is_active.is_(True))
            .filter(CrmAgent.person_id.isnot(None))
            .all()
        )
        for agent in agents:
            person_id = str(agent.person_id)
            if person_id in seen:
                continue
            seen.add(person_id)
            recipient_person_ids.append(person_id)

    if team_uuids:
        members = (
            db.query(CrmAgent.person_id)
            .join(
                CrmAgentTeam,
                and_(
                    CrmAgentTeam.agent_id == CrmAgent.id,
                    CrmAgentTeam.is_active.is_(True),
                ),
            )
            .join(
                CrmTeam,
                and_(
                    CrmTeam.id == CrmAgentTeam.team_id,
                    CrmTeam.is_active.is_(True),
                ),
            )
            .join(
                Person,
                and_(
                    Person.id == CrmAgent.person_id,
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
            .filter(CrmAgent.is_active.is_(True))
            .filter(CrmAgent.person_id.isnot(None))
            .filter(CrmAgentTeam.team_id.in_(team_uuids))
            .all()
        )
        for (person_id,) in members:
            pid = str(person_id)
            if pid in seen:
                continue
            seen.add(pid)
            recipient_person_ids.append(pid)

    return recipient_person_ids
