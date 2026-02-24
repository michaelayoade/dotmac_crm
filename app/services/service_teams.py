from __future__ import annotations

from fastapi import HTTPException
import builtins

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models.person import Person
from app.models.service_team import ServiceTeam, ServiceTeamMember
from app.schemas.service_team import (
    ServiceTeamCreate,
    ServiceTeamMemberCreate,
    ServiceTeamMemberUpdate,
    ServiceTeamUpdate,
)
from app.services.common import apply_is_active_filter, apply_ordering, apply_pagination, coerce_uuid, get_or_404
from app.services.response import ListResponseMixin


class ServiceTeams(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: ServiceTeamCreate) -> ServiceTeam:
        team = ServiceTeam(**payload.model_dump())
        db.add(team)
        db.commit()
        db.refresh(team)
        return team

    @staticmethod
    def get(db: Session, team_id: str) -> ServiceTeam:
        return get_or_404(db, ServiceTeam, team_id)

    @staticmethod
    def list(
        db: Session,
        is_active: bool | None = None,
        search: str | None = None,
        team_type: str | None = None,
        order_by: str = "created_at",
        order_dir: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> list[ServiceTeam]:
        query = db.query(ServiceTeam)
        query = apply_is_active_filter(query, ServiceTeam, is_active)
        if search:
            pattern = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    ServiceTeam.name.ilike(pattern),
                    ServiceTeam.region.ilike(pattern),
                )
            )
        if team_type:
            query = query.filter(ServiceTeam.team_type == team_type)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": ServiceTeam.created_at, "name": ServiceTeam.name},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, team_id: str, payload: ServiceTeamUpdate) -> ServiceTeam:
        team = get_or_404(db, ServiceTeam, team_id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(team, field, value)
        db.commit()
        db.refresh(team)
        return team

    @staticmethod
    def delete(db: Session, team_id: str) -> None:
        team = get_or_404(db, ServiceTeam, team_id)
        team.is_active = False
        db.commit()

    @staticmethod
    def list_designation_region_groups(
        db: Session,
        *,
        is_active: bool | None = True,
        search: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> builtins.list[dict]:
        """Return groups keyed by region + designation with active users attached."""
        query = (
            db.query(ServiceTeam, ServiceTeamMember, Person)
            .outerjoin(
                ServiceTeamMember,
                and_(
                    ServiceTeamMember.team_id == ServiceTeam.id,
                    ServiceTeamMember.is_active.is_(True),
                ),
            )
            .outerjoin(Person, Person.id == ServiceTeamMember.person_id)
        )
        query = apply_is_active_filter(query, ServiceTeam, is_active)

        if search:
            pattern = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    ServiceTeam.name.ilike(pattern),
                    ServiceTeam.region.ilike(pattern),
                    ServiceTeam.erp_department.ilike(pattern),
                    Person.job_title.ilike(pattern),
                    Person.first_name.ilike(pattern),
                    Person.last_name.ilike(pattern),
                    Person.display_name.ilike(pattern),
                    Person.email.ilike(pattern),
                )
            )

        rows = (
            apply_pagination(
                query.order_by(
                    ServiceTeam.name.asc(),
                    Person.first_name.asc(),
                    Person.last_name.asc(),
                ),
                limit,
                offset,
            )
            .all()
        )

        groups_by_key: dict[str, dict] = {}
        for team, member, person in rows:
            region = ((team.region or "").strip() or (person.region if person else None) or "unassigned").strip()
            designation_raw = ((person.job_title if person else None) or "").strip()
            if not designation_raw and member and member.role:
                designation_raw = str(member.role.value).replace("_", " ").strip()
            designation = designation_raw or "unspecified"
            key = f"{region.lower()}_{designation.lower()}"

            group = groups_by_key.get(key)
            if group is None:
                group = {
                    "group_key": key,
                    "region": region,
                    "designation": designation,
                    "members": [],
                }
                groups_by_key[key] = group
            if member and person:
                group["members"].append({"member": member, "person": person})

        groups = list(groups_by_key.values())
        groups.sort(
            key=lambda item: (
                (item.get("region") or "").lower(),
                (item.get("designation") or "").lower(),
            )
        )
        return groups


class ServiceTeamMembers:
    @staticmethod
    def add_member(db: Session, team_id: str, payload: ServiceTeamMemberCreate) -> ServiceTeamMember:
        team = get_or_404(db, ServiceTeam, team_id)
        get_or_404(db, Person, str(payload.person_id), detail="Person not found")

        existing = (
            db.query(ServiceTeamMember)
            .filter(
                ServiceTeamMember.team_id == team.id,
                ServiceTeamMember.person_id == coerce_uuid(payload.person_id),
            )
            .first()
        )
        if existing:
            if not existing.is_active:
                existing.is_active = True
                existing.role = payload.role
                db.commit()
                db.refresh(existing)
                _sync_crm_agents_for_team(db, team.id)
                return existing
            raise HTTPException(status_code=409, detail="Person is already a member of this team")

        member = ServiceTeamMember(
            team_id=team.id,
            person_id=coerce_uuid(payload.person_id),
            role=payload.role,
        )
        db.add(member)
        db.commit()
        db.refresh(member)
        _sync_crm_agents_for_team(db, team.id)
        return member

    @staticmethod
    def remove_member(db: Session, team_id: str, member_id: str) -> None:
        team = get_or_404(db, ServiceTeam, team_id)
        member = get_or_404(db, ServiceTeamMember, member_id, detail="Team member not found")
        if member.team_id != team.id:
            raise HTTPException(status_code=404, detail="Team member not found")
        member.is_active = False
        db.commit()
        _sync_crm_agents_for_team(db, team.id)

    @staticmethod
    def update_member(db: Session, team_id: str, member_id: str, payload: ServiceTeamMemberUpdate) -> ServiceTeamMember:
        team = get_or_404(db, ServiceTeam, team_id)
        member = get_or_404(db, ServiceTeamMember, member_id, detail="Team member not found")
        if member.team_id != team.id:
            raise HTTPException(status_code=404, detail="Team member not found")
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(member, field, value)
        db.commit()
        db.refresh(member)
        return member

    @staticmethod
    def list_members(
        db: Session,
        team_id: str,
        is_active: bool | None = None,
    ) -> list[ServiceTeamMember]:
        get_or_404(db, ServiceTeam, team_id)
        query = db.query(ServiceTeamMember).filter(ServiceTeamMember.team_id == coerce_uuid(team_id))
        query = apply_is_active_filter(query, ServiceTeamMember, is_active)
        return query.order_by(ServiceTeamMember.created_at.asc()).all()

    @staticmethod
    def get_person_teams(db: Session, person_id: str) -> list[ServiceTeamMember]:
        return (
            db.query(ServiceTeamMember)
            .filter(
                ServiceTeamMember.person_id == coerce_uuid(person_id),
                ServiceTeamMember.is_active.is_(True),
            )
            .all()
        )


def _sync_crm_agents_for_team(db: Session, service_team_id) -> None:
    """Keep CrmAgentTeam in sync when ServiceTeam membership changes.

    When a CrmTeam is linked to a ServiceTeam (via service_team_id FK),
    this ensures CrmAgent + CrmAgentTeam records match the ServiceTeam members.
    """
    from app.models.crm.team import CrmAgent, CrmAgentTeam, CrmTeam

    crm_teams = (
        db.query(CrmTeam)
        .filter(
            CrmTeam.service_team_id == service_team_id,
            CrmTeam.is_active.is_(True),
        )
        .all()
    )
    if not crm_teams:
        return

    active_members = (
        db.query(ServiceTeamMember)
        .filter(
            ServiceTeamMember.team_id == service_team_id,
            ServiceTeamMember.is_active.is_(True),
        )
        .all()
    )
    active_person_ids = {m.person_id for m in active_members}

    for crm_team in crm_teams:
        for person_id in active_person_ids:
            agent = db.query(CrmAgent).filter(CrmAgent.person_id == person_id).first()
            if not agent:
                agent = CrmAgent(person_id=person_id, is_active=True)
                db.add(agent)
                db.flush()

            link = (
                db.query(CrmAgentTeam)
                .filter(
                    CrmAgentTeam.agent_id == agent.id,
                    CrmAgentTeam.team_id == crm_team.id,
                )
                .first()
            )
            if link:
                if not link.is_active:
                    link.is_active = True
            else:
                db.add(CrmAgentTeam(agent_id=agent.id, team_id=crm_team.id, is_active=True))

        existing_links = (
            db.query(CrmAgentTeam)
            .join(CrmAgent)
            .filter(CrmAgentTeam.team_id == crm_team.id, CrmAgentTeam.is_active.is_(True))
            .all()
        )
        for link in existing_links:
            agent = db.get(CrmAgent, link.agent_id)
            if agent and agent.person_id not in active_person_ids:
                link.is_active = False

    db.commit()


def sync_crm_agents(db: Session, service_team_id: str) -> dict:
    """Public API for syncing CRM agents from a ServiceTeam."""
    _sync_crm_agents_for_team(db, coerce_uuid(service_team_id))
    return {"synced": True}


service_teams = ServiceTeams()
service_team_members = ServiceTeamMembers()
