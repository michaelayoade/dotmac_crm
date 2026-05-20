"""Shared visibility scoping for Workqueue providers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import false, func, or_
from sqlalchemy.orm import Session, aliased

from app.models.crm.conversation import Conversation, ConversationAssignment
from app.models.crm.sales import Lead, Quote
from app.models.crm.team import CrmAgent, CrmAgentTeam, CrmTeam
from app.models.projects import Project, ProjectTask, ProjectTaskAssignee
from app.models.service_team import ServiceTeam, ServiceTeamMember, ServiceTeamMemberRole
from app.models.tickets import Ticket, TicketAssignee
from app.services.workqueue.types import WorkqueueAudience

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkqueueScope:
    person_id: UUID
    person_region: str | None
    audience: WorkqueueAudience
    roles: frozenset[str]
    permissions: frozenset[str]
    crm_agent_ids: frozenset[UUID]
    accessible_service_team_ids: frozenset[UUID]
    accessible_service_team_regions: frozenset[str]
    accessible_crm_team_ids: frozenset[UUID]
    accessible_crm_agent_ids: frozenset[UUID]
    accessible_person_ids: frozenset[UUID]
    is_admin: bool

    @property
    def department_ids(self) -> tuple[str, ...]:
        return tuple(str(team_id) for team_id in sorted(self.accessible_service_team_ids, key=str))

    @property
    def crm_team_ids(self) -> tuple[str, ...]:
        return tuple(str(team_id) for team_id in sorted(self.accessible_crm_team_ids, key=str))

    @property
    def applied_filters(self) -> dict[str, object]:
        return {
            "audience": self.audience.value,
            "person_region": self.person_region,
            "crm_agent_ids": [str(agent_id) for agent_id in sorted(self.crm_agent_ids, key=str)],
            "service_team_ids": list(self.department_ids),
            "service_team_regions": sorted(self.accessible_service_team_regions),
            "crm_team_ids": list(self.crm_team_ids),
            "accessible_crm_agent_ids": [str(agent_id) for agent_id in sorted(self.accessible_crm_agent_ids, key=str)],
            "accessible_person_ids": [str(person_id) for person_id in sorted(self.accessible_person_ids, key=str)],
            "is_admin": self.is_admin,
        }


def _log_scope_decision(scope: WorkqueueScope, module: str, decision: str, **details) -> None:
    logger.info(
        "workqueue_scope_decision user_id=%s module=%s audience=%s decision=%s details=%s",
        scope.person_id,
        module,
        scope.audience.value,
        decision,
        details,
    )


def _active_person_service_team_ids(db: Session, person_id: UUID) -> set[UUID]:
    return {
        row[0]
        for row in (
            db.query(ServiceTeamMember.team_id)
            .filter(ServiceTeamMember.person_id == person_id)
            .filter(ServiceTeamMember.is_active.is_(True))
            .all()
        )
    }


def _managed_service_team_ids(db: Session, person_id: UUID) -> set[UUID]:
    membership_ids = {
        row[0]
        for row in (
            db.query(ServiceTeamMember.team_id)
            .filter(ServiceTeamMember.person_id == person_id)
            .filter(ServiceTeamMember.is_active.is_(True))
            .filter(ServiceTeamMember.role.in_((ServiceTeamMemberRole.lead, ServiceTeamMemberRole.manager)))
            .all()
        )
    }
    manager_ids = {
        row[0]
        for row in (
            db.query(ServiceTeam.id)
            .filter(ServiceTeam.manager_person_id == person_id)
            .filter(ServiceTeam.is_active.is_(True))
            .all()
        )
    }
    return membership_ids | manager_ids


def _service_team_regions(db: Session, service_team_ids: set[UUID]) -> set[str]:
    if not service_team_ids:
        return set()
    return {
        region.strip().lower()
        for (region,) in (
            db.query(ServiceTeam.region)
            .filter(ServiceTeam.id.in_(service_team_ids))
            .filter(ServiceTeam.is_active.is_(True))
            .filter(ServiceTeam.region.isnot(None))
            .all()
        )
        if region and region.strip()
    }


def _person_crm_agent_ids(db: Session, person_id: UUID) -> set[UUID]:
    return {
        row[0]
        for row in (
            db.query(CrmAgent.id).filter(CrmAgent.person_id == person_id).filter(CrmAgent.is_active.is_(True)).all()
        )
    }


def _crm_team_ids_for_service_teams(db: Session, service_team_ids: set[UUID]) -> set[UUID]:
    if not service_team_ids:
        return set()
    return {
        row[0]
        for row in (
            db.query(CrmTeam.id)
            .filter(CrmTeam.service_team_id.in_(service_team_ids))
            .filter(CrmTeam.is_active.is_(True))
            .all()
        )
    }


def _crm_team_ids_for_agents(db: Session, crm_agent_ids: set[UUID]) -> set[UUID]:
    if not crm_agent_ids:
        return set()
    return {
        row[0]
        for row in (
            db.query(CrmAgentTeam.team_id)
            .join(CrmTeam, CrmTeam.id == CrmAgentTeam.team_id)
            .filter(CrmAgentTeam.agent_id.in_(crm_agent_ids))
            .filter(CrmAgentTeam.is_active.is_(True))
            .filter(CrmTeam.is_active.is_(True))
            .all()
        )
    }


def _crm_agent_ids_for_teams(db: Session, crm_team_ids: set[UUID]) -> set[UUID]:
    if not crm_team_ids:
        return set()
    return {
        row[0]
        for row in (
            db.query(CrmAgentTeam.agent_id)
            .join(CrmAgent, CrmAgent.id == CrmAgentTeam.agent_id)
            .filter(CrmAgentTeam.team_id.in_(crm_team_ids))
            .filter(CrmAgentTeam.is_active.is_(True))
            .filter(CrmAgent.is_active.is_(True))
            .all()
        )
    }


def _team_member_person_ids(db: Session, service_team_ids: set[UUID]) -> set[UUID]:
    if not service_team_ids:
        return set()
    return {
        row[0]
        for row in (
            db.query(ServiceTeamMember.person_id)
            .filter(ServiceTeamMember.team_id.in_(service_team_ids))
            .filter(ServiceTeamMember.is_active.is_(True))
            .all()
        )
    }


def _crm_agent_person_ids(db: Session, crm_agent_ids: set[UUID]) -> set[UUID]:
    if not crm_agent_ids:
        return set()
    return {
        row[0]
        for row in (
            db.query(CrmAgent.person_id)
            .filter(CrmAgent.id.in_(crm_agent_ids))
            .filter(CrmAgent.person_id.isnot(None))
            .all()
        )
    }


def _normalize_region(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def get_workqueue_scope(db: Session, user, audience_mode: WorkqueueAudience) -> WorkqueueScope:
    roles = {str(role).strip().lower() for role in (getattr(user, "roles", None) or []) if str(role).strip()}
    permissions = {
        str(permission).strip() for permission in (getattr(user, "permissions", None) or []) if str(permission).strip()
    }
    person_id = user.person_id
    crm_agent_ids = _person_crm_agent_ids(db, person_id)

    member_service_team_ids = _active_person_service_team_ids(db, person_id)
    managed_service_team_ids = _managed_service_team_ids(db, person_id)
    accessible_service_team_ids = member_service_team_ids | managed_service_team_ids
    accessible_service_team_regions = _service_team_regions(db, accessible_service_team_ids)

    member_crm_team_ids = _crm_team_ids_for_agents(db, crm_agent_ids)
    linked_crm_team_ids = _crm_team_ids_for_service_teams(db, accessible_service_team_ids)
    accessible_crm_team_ids = member_crm_team_ids | linked_crm_team_ids
    accessible_crm_agent_ids = _crm_agent_ids_for_teams(db, accessible_crm_team_ids) | crm_agent_ids
    accessible_person_ids = (
        _team_member_person_ids(db, accessible_service_team_ids)
        | _crm_agent_person_ids(db, accessible_crm_agent_ids)
        | {person_id}
    )

    is_admin = "admin" in roles or "workqueue:audience:org" in permissions
    scope = WorkqueueScope(
        person_id=person_id,
        person_region=_normalize_region(getattr(user, "region", None)),
        audience=audience_mode,
        roles=frozenset(roles),
        permissions=frozenset(permissions),
        crm_agent_ids=frozenset(crm_agent_ids),
        accessible_service_team_ids=frozenset(accessible_service_team_ids),
        accessible_service_team_regions=frozenset(accessible_service_team_regions),
        accessible_crm_team_ids=frozenset(accessible_crm_team_ids),
        accessible_crm_agent_ids=frozenset(accessible_crm_agent_ids),
        accessible_person_ids=frozenset(accessible_person_ids),
        is_admin=is_admin,
    )
    logger.info(
        "workqueue_scope_resolved user_id=%s department_ids=%s audience=%s applied_filters=%s",
        person_id,
        list(scope.department_ids),
        audience_mode.value,
        scope.applied_filters,
    )
    return scope


def apply_conversation_scope(stmt, scope: WorkqueueScope):
    assignment_agent = aliased(CrmAgent)

    if scope.audience is WorkqueueAudience.self_:
        _log_scope_decision(
            scope,
            "conversation",
            "include_direct_assignment",
            assignment_source="active_assignment_agent_person",
        )
        return (
            stmt.join(
                ConversationAssignment,
                ConversationAssignment.conversation_id == Conversation.id,
            )
            .join(assignment_agent, assignment_agent.id == ConversationAssignment.agent_id)
            .where(ConversationAssignment.is_active.is_(True))
            .where(assignment_agent.person_id == scope.person_id)
        )

    if scope.audience is WorkqueueAudience.team:
        filters = [assignment_agent.person_id == scope.person_id]
        if scope.accessible_person_ids:
            filters.append(assignment_agent.person_id.in_(scope.accessible_person_ids))
        if scope.accessible_crm_team_ids:
            filters.append(ConversationAssignment.team_id.in_(scope.accessible_crm_team_ids))
        if len(filters) == 1 and not scope.accessible_crm_team_ids:
            _log_scope_decision(
                scope,
                "conversation",
                "team_visibility_limited_to_direct_assignment",
                resolved_assignment_source="current_user_only",
            )
        else:
            _log_scope_decision(
                scope,
                "conversation",
                "include_direct_assignment_or_team_visibility",
                resolved_assignment_source="active_assignment_agent_person",
                resolved_team_source="conversation_assignment.team_id",
            )

        return (
            stmt.join(
                ConversationAssignment,
                ConversationAssignment.conversation_id == Conversation.id,
            )
            .outerjoin(assignment_agent, assignment_agent.id == ConversationAssignment.agent_id)
            .where(ConversationAssignment.is_active.is_(True))
            .where(or_(*filters))
        )

    _log_scope_decision(
        scope,
        "conversation",
        "include_all_active_inbox_records",
    )
    return stmt


def apply_ticket_scope(stmt, scope: WorkqueueScope):
    if scope.audience is WorkqueueAudience.self_:
        filters = [
            Ticket.assigned_to_person_id == scope.person_id,
            TicketAssignee.person_id == scope.person_id,
        ]
        if scope.person_region:
            filters.append(func.lower(func.trim(Ticket.region)) == scope.person_region.lower())
        _log_scope_decision(
            scope,
            "ticket",
            "include_direct_assignment_or_person_region",
            resolved_assignment_source="ticket_assignee_or_assigned_to_person_id",
            resolved_region_source="person.region",
        )
        return stmt.outerjoin(TicketAssignee, TicketAssignee.ticket_id == Ticket.id).where(or_(*filters))

    if scope.audience is WorkqueueAudience.team:
        filters = [
            Ticket.assigned_to_person_id == scope.person_id,
            TicketAssignee.person_id == scope.person_id,
        ]
        if scope.accessible_service_team_ids:
            filters.append(Ticket.service_team_id.in_(scope.accessible_service_team_ids))
        region_filters = set(scope.accessible_service_team_regions)
        if scope.person_region:
            region_filters.add(scope.person_region.lower())
        if region_filters:
            filters.append(func.lower(func.trim(Ticket.region)).in_(region_filters))
        _log_scope_decision(
            scope,
            "ticket",
            "include_direct_assignment_or_service_team_or_region",
            resolved_assignment_source="ticket_assignee_or_assigned_to_person_id",
            resolved_team_source="ticket.service_team_id",
            resolved_region_source="person.region_or_service_team.region",
        )
        return stmt.outerjoin(TicketAssignee, TicketAssignee.ticket_id == Ticket.id).where(or_(*filters))

    _log_scope_decision(
        scope,
        "ticket",
        "include_owned_records_only",
        resolved_team_source="ticket.service_team_id",
    )
    return stmt.where(Ticket.service_team_id.isnot(None))


def apply_lead_scope(stmt, scope: WorkqueueScope):
    owner_agent = aliased(CrmAgent)
    stmt = stmt.outerjoin(owner_agent, owner_agent.id == Lead.owner_agent_id)

    if scope.audience is WorkqueueAudience.self_:
        if not scope.person_region:
            _log_scope_decision(scope, "lead", "exclude_all", reason="missing_person_region")
            return stmt.where(false())
        _log_scope_decision(
            scope,
            "lead",
            "include_person_region",
            resolved_region_source="person.region",
        )
        return stmt.where(func.lower(func.trim(Lead.region)) == scope.person_region.lower())

    if scope.audience is WorkqueueAudience.team:
        filters = []
        if scope.accessible_person_ids:
            filters.append(owner_agent.person_id.in_(scope.accessible_person_ids))
        if scope.accessible_service_team_regions:
            filters.append(func.lower(func.trim(Lead.region)).in_(scope.accessible_service_team_regions))
        if not filters:
            _log_scope_decision(scope, "lead", "exclude_all", reason="no_accessible_people_or_regions")
            return stmt.where(false())
        _log_scope_decision(
            scope,
            "lead",
            "include_team_profile_or_region_records",
            resolved_profile_source="lead.owner_agent.person_id",
            resolved_region_source="service_team.region",
        )
        return stmt.where(or_(*filters))

    _log_scope_decision(
        scope,
        "lead",
        "include_all_active_records",
    )
    return stmt


def apply_quote_scope(stmt, scope: WorkqueueScope):
    lead_alias = aliased(Lead)
    lead_owner_agent = aliased(CrmAgent)
    stmt = stmt.outerjoin(lead_alias, lead_alias.id == Quote.lead_id).outerjoin(
        lead_owner_agent, lead_owner_agent.id == lead_alias.owner_agent_id
    )
    owner_person_ids = [str(person_id) for person_id in scope.accessible_person_ids]

    if scope.audience is WorkqueueAudience.self_:
        _log_scope_decision(
            scope,
            "quote",
            "include_profile_owned_records",
            resolved_profile_source="quote.owner_person_id_or_metadata_owner_or_lead.owner_agent.person_id",
        )
        return stmt.where(
            or_(
                Quote.owner_person_id == scope.person_id,
                Quote.metadata_["owner_person_id"].as_string() == str(scope.person_id),
                lead_owner_agent.person_id == scope.person_id,
            )
        )

    if scope.audience is WorkqueueAudience.team:
        quote_filters = []
        if owner_person_ids:
            quote_filters.append(Quote.owner_person_id.in_(scope.accessible_person_ids))
            quote_filters.append(Quote.metadata_["owner_person_id"].as_string().in_(owner_person_ids))
            quote_filters.append(lead_owner_agent.person_id.in_(scope.accessible_person_ids))
        if not quote_filters:
            _log_scope_decision(scope, "quote", "exclude_all", reason="no_accessible_people")
            return stmt.where(false())
        _log_scope_decision(
            scope,
            "quote",
            "include_team_profile_owned_records",
            resolved_profile_source="quote.owner_person_id_or_metadata_owner_or_lead.owner_agent.person_id",
        )
        return stmt.where(or_(*quote_filters))

    _log_scope_decision(
        scope,
        "quote",
        "include_owned_records_only",
        resolved_profile_source="quote.owner_person_id_or_metadata_owner_or_lead.owner_agent_id",
    )
    return stmt.where(
        or_(
            Quote.owner_person_id.isnot(None),
            Quote.metadata_["owner_person_id"].as_string().isnot(None),
            lead_alias.owner_agent_id.isnot(None),
        )
    )


def apply_task_scope(stmt, scope: WorkqueueScope):
    if scope.audience is WorkqueueAudience.self_:
        _log_scope_decision(
            scope,
            "task",
            "include_direct_assignment",
            resolved_assignment_source="task_assignee_or_assigned_to_person_id",
        )
        return stmt.outerjoin(ProjectTaskAssignee, ProjectTaskAssignee.task_id == ProjectTask.id).where(
            or_(
                ProjectTaskAssignee.person_id == scope.person_id,
                ProjectTask.assigned_to_person_id == scope.person_id,
            )
        )

    stmt = stmt.join(Project, Project.id == ProjectTask.project_id)
    if scope.audience is WorkqueueAudience.team:
        filters = [
            ProjectTask.assigned_to_person_id == scope.person_id,
            ProjectTaskAssignee.person_id == scope.person_id,
        ]
        if scope.accessible_person_ids:
            filters.append(ProjectTask.assigned_to_person_id.in_(scope.accessible_person_ids))
            filters.append(ProjectTaskAssignee.person_id.in_(scope.accessible_person_ids))
        if scope.accessible_service_team_ids:
            filters.append(Project.service_team_id.in_(scope.accessible_service_team_ids))
        _log_scope_decision(
            scope,
            "task",
            "include_direct_assignment_or_service_team_or_profile",
            resolved_assignment_source="task_assignee_or_assigned_to_person_id",
            resolved_team_source="project.service_team_id",
            resolved_profile_source="task.assigned_person",
        )
        return stmt.outerjoin(ProjectTaskAssignee, ProjectTaskAssignee.task_id == ProjectTask.id).where(or_(*filters))

    _log_scope_decision(
        scope,
        "task",
        "include_owned_records_only",
        resolved_team_source="project.service_team_id",
    )
    return stmt.where(Project.service_team_id.isnot(None))
