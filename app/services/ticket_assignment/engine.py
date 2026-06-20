"""Ticket rule-based assignment engine."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.person import Person
from app.models.projects import Project, ProjectTask, ProjectTaskAssignee
from app.models.tickets import Ticket, TicketAssignee
from app.models.workflow import TicketAssignmentRule, TicketAssignmentStrategy
from app.services.common import coerce_uuid
from app.services.ticket_assignment.rules import build_context, build_project_context, list_active_rules, matches_rule
from app.services.ticket_assignment.selectors import (
    list_team_candidate_person_ids,
    pick_least_loaded,
    pick_round_robin,
    resolve_assignment_candidate_guards,
)


@dataclass(frozen=True)
class AssignmentResult:
    assigned: bool
    ticket_id: str | None = None
    project_id: str | None = None
    rule_id: str | None = None
    rule_name: str | None = None
    strategy: str | None = None
    assignment_target: str | None = None
    candidate_count: int = 0
    assignee_person_id: str | None = None
    fallback_service_team_id: str | None = None
    reason: str | None = None


def auto_assign_ticket(
    db: Session,
    ticket_id: str,
    *,
    trigger: str = "create",
    actor_person_id: str | None = None,
) -> AssignmentResult:
    """Try to auto-assign a ticket using active ticket assignment rules."""
    results = auto_assign_ticket_all(db, ticket_id, trigger=trigger, actor_person_id=actor_person_id)
    assigned_result = next((result for result in results if result.assigned), None)
    return assigned_result or results[0]


def auto_assign_ticket_all(
    db: Session,
    ticket_id: str,
    *,
    trigger: str = "create",
    actor_person_id: str | None = None,
) -> list[AssignmentResult]:
    """Apply all compatible direct ticket assignment rules, preserving legacy team assignment."""
    del trigger
    del actor_person_id

    ticket = db.get(Ticket, coerce_uuid(ticket_id))
    if not ticket or not ticket.is_active:
        return [AssignmentResult(assigned=False, ticket_id=ticket_id, reason="ticket_not_found_or_inactive")]

    ctx = build_context(ticket)
    require_presence, max_open_tickets = resolve_assignment_candidate_guards(db)
    rules = list_active_rules(db)
    last_matched_rule: TicketAssignmentRule | None = None
    last_candidate_count = 0
    results: list[AssignmentResult] = []
    for rule in rules:
        if not matches_rule(rule, ctx):
            continue
        last_matched_rule = rule
        direct_result = _apply_direct_ticket_assignment(db, ticket=ticket, rule=rule)
        if direct_result:
            results.append(direct_result)
            continue
        if ticket.assigned_to_person_id:
            continue
        assignee, candidate_count = _select_assignee(
            db,
            ticket=ticket,
            rule=rule,
            require_presence=require_presence,
            max_open_tickets=max_open_tickets,
        )
        last_candidate_count = candidate_count
        if not assignee:
            if rule.team_id and not ticket.service_team_id:
                ticket.service_team_id = coerce_uuid(str(rule.team_id))
                db.commit()
                db.refresh(ticket)
                results.append(
                    AssignmentResult(
                        assigned=False,
                        ticket_id=str(ticket.id),
                        rule_id=str(rule.id),
                        rule_name=rule.name,
                        strategy=rule.strategy.value if rule.strategy else None,
                        assignment_target="technician",
                        candidate_count=candidate_count,
                        fallback_service_team_id=str(rule.team_id),
                        reason="queue_fallback_team_assigned",
                    )
                )
                continue
            continue
        ticket.assigned_to_person_id = coerce_uuid(assignee)
        db.commit()
        db.refresh(ticket)
        results.append(
            AssignmentResult(
                assigned=True,
                ticket_id=str(ticket.id),
                rule_id=str(rule.id),
                rule_name=rule.name,
                strategy=rule.strategy.value if rule.strategy else None,
                assignment_target="technician",
                candidate_count=candidate_count,
                assignee_person_id=assignee,
                reason="assigned",
            )
        )
    if results:
        return results

    if last_matched_rule is not None:
        return [
            AssignmentResult(
                assigned=False,
                ticket_id=str(ticket.id),
                rule_id=str(last_matched_rule.id),
                rule_name=last_matched_rule.name,
                strategy=last_matched_rule.strategy.value if last_matched_rule.strategy else None,
                assignment_target=_assignment_target(last_matched_rule),
                candidate_count=last_candidate_count,
                assignee_person_id=str(ticket.assigned_to_person_id) if ticket.assigned_to_person_id else None,
                reason="already_assigned" if ticket.assigned_to_person_id else "no_eligible_candidates",
            )
        ]
    return [AssignmentResult(assigned=False, ticket_id=str(ticket.id), reason="no_matching_rule")]


def auto_assign_project(
    db: Session,
    project_id: str,
    *,
    trigger: str = "create",
    actor_person_id: str | None = None,
) -> list[AssignmentResult]:
    """Apply active workflow assignment rules to a project."""
    del trigger
    del actor_person_id

    project = db.get(Project, coerce_uuid(project_id))
    if not project or not project.is_active:
        return [AssignmentResult(assigned=False, project_id=project_id, reason="project_not_found_or_inactive")]

    ctx = build_project_context(project)
    results: list[AssignmentResult] = []
    for rule in list_active_rules(db):
        if not matches_rule(rule, ctx):
            continue
        result = _apply_direct_project_assignment(db, project=project, rule=rule)
        if result:
            results.append(result)
    if not results:
        return [AssignmentResult(assigned=False, project_id=str(project.id), reason="no_matching_rule")]
    return results


def _select_assignee(
    db: Session,
    *,
    ticket: Ticket,
    rule: TicketAssignmentRule,
    require_presence: bool,
    max_open_tickets: int | None,
) -> tuple[str | None, int]:
    team_id = str(rule.team_id) if rule.team_id else (str(ticket.service_team_id) if ticket.service_team_id else None)
    candidates = list_team_candidate_person_ids(
        db,
        team_id,
        require_presence=require_presence,
        max_open_tickets=max_open_tickets,
    )
    if not candidates:
        return None, 0
    if rule.strategy == TicketAssignmentStrategy.least_loaded:
        return pick_least_loaded(db, candidates), len(candidates)
    return pick_round_robin(db, rule_id=str(rule.id), person_ids=candidates), len(candidates)


def _assignment_config(rule: TicketAssignmentRule) -> dict:
    return rule.match_config if isinstance(rule.match_config, dict) else {}


def _assignment_target(rule: TicketAssignmentRule) -> str:
    return str(_assignment_config(rule).get("assignment_target") or "technician").strip().lower()


def _assignee_person_id(rule: TicketAssignmentRule) -> str | None:
    value = str(_assignment_config(rule).get("assignee_person_id") or "").strip()
    return value or None


def _person_exists(db: Session, person_id: str) -> bool:
    return db.get(Person, coerce_uuid(person_id)) is not None


def _apply_direct_ticket_assignment(
    db: Session,
    *,
    ticket: Ticket,
    rule: TicketAssignmentRule,
) -> AssignmentResult | None:
    assignee = _assignee_person_id(rule)
    if not assignee:
        return None
    if not _person_exists(db, assignee):
        return AssignmentResult(
            assigned=False,
            ticket_id=str(ticket.id),
            rule_id=str(rule.id),
            rule_name=rule.name,
            assignment_target=_assignment_target(rule),
            assignee_person_id=assignee,
            reason="assignee_not_found",
        )

    target = _assignment_target(rule)
    changed = False
    if target == "technical_supervisor":
        if not ticket.ticket_manager_person_id:
            ticket.ticket_manager_person_id = coerce_uuid(assignee)
            changed = True
    else:
        assignee_uuid = coerce_uuid(assignee)
        if not ticket.assigned_to_person_id:
            ticket.assigned_to_person_id = assignee_uuid
            changed = True
        if not any(str(existing.person_id) == assignee for existing in ticket.assignees):
            ticket.assignees.append(TicketAssignee(ticket_id=ticket.id, person_id=assignee_uuid))
            changed = True

    if changed:
        db.commit()
        db.refresh(ticket)
    return AssignmentResult(
        assigned=changed,
        ticket_id=str(ticket.id),
        rule_id=str(rule.id),
        rule_name=rule.name,
        strategy="direct",
        assignment_target=target,
        candidate_count=1,
        assignee_person_id=assignee,
        reason="assigned" if changed else "already_assigned",
    )


def _apply_direct_project_assignment(
    db: Session,
    *,
    project: Project,
    rule: TicketAssignmentRule,
) -> AssignmentResult | None:
    assignee = _assignee_person_id(rule)
    if not assignee:
        return None
    target = _assignment_target(rule)
    if not _person_exists(db, assignee):
        return AssignmentResult(
            assigned=False,
            project_id=str(project.id),
            rule_id=str(rule.id),
            rule_name=rule.name,
            assignment_target=target,
            assignee_person_id=assignee,
            reason="assignee_not_found",
        )

    changed = False
    if target == "technical_supervisor":
        if not project.manager_person_id:
            project.manager_person_id = coerce_uuid(assignee)
            changed = True
        if not project.project_manager_person_id:
            project.project_manager_person_id = coerce_uuid(assignee)
            changed = True
    elif target == "technician":
        assignee_uuid = coerce_uuid(assignee)
        tasks = (
            db.query(ProjectTask)
            .filter(ProjectTask.project_id == project.id)
            .filter(ProjectTask.is_active.is_(True))
            .all()
        )
        for task in tasks:
            if not task.assigned_to_person_id:
                task.assigned_to_person_id = assignee_uuid
                changed = True
            if not any(str(existing.person_id) == assignee for existing in task.assignees):
                task.assignees.append(ProjectTaskAssignee(task_id=task.id, person_id=assignee_uuid))
                changed = True
    else:
        return AssignmentResult(
            assigned=False,
            project_id=str(project.id),
            rule_id=str(rule.id),
            rule_name=rule.name,
            assignment_target=target,
            assignee_person_id=assignee,
            reason="unsupported_assignment_target",
        )

    if changed:
        db.commit()
        db.refresh(project)
    return AssignmentResult(
        assigned=changed,
        project_id=str(project.id),
        rule_id=str(rule.id),
        rule_name=rule.name,
        strategy="direct",
        assignment_target=target,
        candidate_count=1,
        assignee_person_id=assignee,
        reason="assigned" if changed else "already_assigned",
    )
