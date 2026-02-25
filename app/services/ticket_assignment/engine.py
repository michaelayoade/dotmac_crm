"""Ticket rule-based assignment engine."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.tickets import Ticket
from app.models.workflow import TicketAssignmentRule, TicketAssignmentStrategy
from app.services.common import coerce_uuid
from app.services.ticket_assignment.rules import build_context, list_active_rules, matches_rule
from app.services.ticket_assignment.selectors import (
    list_team_candidate_person_ids,
    pick_least_loaded,
    pick_round_robin,
)


@dataclass(frozen=True)
class AssignmentResult:
    assigned: bool
    ticket_id: str
    rule_id: str | None = None
    assignee_person_id: str | None = None
    reason: str | None = None


def auto_assign_ticket(
    db: Session,
    ticket_id: str,
    *,
    trigger: str = "create",
    actor_person_id: str | None = None,
) -> AssignmentResult:
    """Try to auto-assign a ticket using active ticket assignment rules."""
    del trigger
    del actor_person_id

    ticket = db.get(Ticket, coerce_uuid(ticket_id))
    if not ticket or not ticket.is_active:
        return AssignmentResult(assigned=False, ticket_id=ticket_id, reason="ticket_not_found_or_inactive")
    if ticket.assigned_to_person_id:
        return AssignmentResult(
            assigned=False,
            ticket_id=str(ticket.id),
            assignee_person_id=str(ticket.assigned_to_person_id),
            reason="already_assigned",
        )

    ctx = build_context(ticket)
    rules = list_active_rules(db)
    for rule in rules:
        if not matches_rule(rule, ctx):
            continue
        assignee = _select_assignee(db, ticket=ticket, rule=rule)
        if not assignee:
            continue
        ticket.assigned_to_person_id = coerce_uuid(assignee)
        db.commit()
        db.refresh(ticket)
        return AssignmentResult(
            assigned=True,
            ticket_id=str(ticket.id),
            rule_id=str(rule.id),
            assignee_person_id=assignee,
            reason="assigned",
        )

    return AssignmentResult(assigned=False, ticket_id=str(ticket.id), reason="no_matching_rule_or_candidate")


def _select_assignee(db: Session, *, ticket: Ticket, rule: TicketAssignmentRule) -> str | None:
    team_id = str(rule.team_id) if rule.team_id else (str(ticket.service_team_id) if ticket.service_team_id else None)
    candidates = list_team_candidate_person_ids(db, team_id)
    if not candidates:
        return None
    if rule.strategy == TicketAssignmentStrategy.least_loaded:
        return pick_least_loaded(db, candidates)
    return pick_round_robin(db, rule_id=str(rule.id), person_ids=candidates)
