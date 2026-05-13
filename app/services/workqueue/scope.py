"""Shared visibility scoping for Workqueue providers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import false, or_
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
    audience: WorkqueueAudience
    roles: frozenset[str]
    permissions: frozenset[str]
    crm_agent_ids: frozenset[UUID]
    accessible_service_team_ids: frozenset[UUID]
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
            "crm_agent_ids": [str(agent_id) for agent_id in sorted(self.crm_agent_ids, key=str)],
            "service_team_ids": list(self.department_ids),
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
        audience=audience_mode,
        roles=frozenset(roles),
        permissions=frozenset(permissions),
        crm_agent_ids=frozenset(crm_agent_ids),
        accessible_service_team_ids=frozenset(accessible_service_team_ids),
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
        "include_owned_records_only",
        resolved_assignment_source="active_assignment_agent_or_team",
    )
    return (
        stmt.join(
            ConversationAssignment,
            ConversationAssignment.conversation_id == Conversation.id,
        )
        .where(ConversationAssignment.is_active.is_(True))
        .where(or_(ConversationAssignment.team_id.isnot(None), ConversationAssignment.agent_id.isnot(None)))
    )


def apply_ticket_scope(stmt, scope: WorkqueueScope):
    if scope.audience is WorkqueueAudience.self_:
        _log_scope_decision(
            scope,
            "ticket",
            "include_direct_assignment",
            resolved_assignment_source="ticket_assignee_or_assigned_to_person_id",
        )
        return stmt.outerjoin(TicketAssignee, TicketAssignee.ticket_id == Ticket.id).where(
            or_(TicketAssignee.person_id == scope.person_id, Ticket.assigned_to_person_id == scope.person_id)
        )

    if scope.audience is WorkqueueAudience.team:
        filters = [
            Ticket.assigned_to_person_id == scope.person_id,
            TicketAssignee.person_id == scope.person_id,
        ]
        if scope.accessible_service_team_ids:
            filters.append(Ticket.service_team_id.in_(scope.accessible_service_team_ids))
        _log_scope_decision(
            scope,
            "ticket",
            "include_direct_assignment_or_service_team",
            resolved_assignment_source="ticket_assignee_or_assigned_to_person_id",
            resolved_team_source="ticket.service_team_id",
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
        _log_scope_decision(
            scope,
            "lead",
            "include_profile_owned_records",
            resolved_profile_source="lead.owner_agent.person_id",
        )
        return stmt.where(owner_agent.person_id == scope.person_id)

    if scope.audience is WorkqueueAudience.team:
        if not scope.accessible_person_ids:
            _log_scope_decision(scope, "lead", "exclude_all", reason="no_accessible_people")
            return stmt.where(false())
        _log_scope_decision(
            scope,
            "lead",
            "include_team_profile_owned_records",
            resolved_profile_source="lead.owner_agent.person_id",
        )
        return stmt.where(owner_agent.person_id.in_(scope.accessible_person_ids))

    _log_scope_decision(
        scope,
        "lead",
        "include_owned_records_only",
        resolved_profile_source="lead.owner_agent.person_id",
    )
    return stmt.where(Lead.owner_agent_id.isnot(None))


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
            resolved_profile_source="quote.metadata.owner_person_id_or_lead.owner_agent.person_id",
        )
        return stmt.where(
            or_(
                Quote.metadata_["owner_person_id"].as_string() == str(scope.person_id),
                lead_owner_agent.person_id == scope.person_id,
            )
        )

    if scope.audience is WorkqueueAudience.team:
        quote_filters = []
        if owner_person_ids:
            quote_filters.append(Quote.metadata_["owner_person_id"].as_string().in_(owner_person_ids))
            quote_filters.append(lead_owner_agent.person_id.in_(scope.accessible_person_ids))
        if not quote_filters:
            _log_scope_decision(scope, "quote", "exclude_all", reason="no_accessible_people")
            return stmt.where(false())
        _log_scope_decision(
            scope,
            "quote",
            "include_team_profile_owned_records",
            resolved_profile_source="quote.metadata.owner_person_id_or_lead.owner_agent.person_id",
        )
        return stmt.where(or_(*quote_filters))

    _log_scope_decision(
        scope,
        "quote",
        "include_owned_records_only",
        resolved_profile_source="quote.metadata.owner_person_id_or_lead.owner_agent_id",
    )
    return stmt.where(
        or_(Quote.metadata_["owner_person_id"].as_string().isnot(None), lead_alias.owner_agent_id.isnot(None))
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
