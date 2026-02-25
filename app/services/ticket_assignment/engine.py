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
    resolve_assignment_candidate_guards,
)


@dataclass(frozen=True)
class AssignmentResult:
    assigned: bool
    ticket_id: str
    rule_id: str | None = None
    rule_name: str | None = None
    strategy: str | None = None
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
    require_presence, max_open_tickets = resolve_assignment_candidate_guards(db)
    rules = list_active_rules(db)
    last_matched_rule: TicketAssignmentRule | None = None
    last_candidate_count = 0
    for rule in rules:
        if not matches_rule(rule, ctx):
            continue
        last_matched_rule = rule
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
                return AssignmentResult(
                    assigned=False,
                    ticket_id=str(ticket.id),
                    rule_id=str(rule.id),
                    rule_name=rule.name,
                    strategy=rule.strategy.value if rule.strategy else None,
                    candidate_count=candidate_count,
                    fallback_service_team_id=str(rule.team_id),
                    reason="queue_fallback_team_assigned",
                )
            continue
        ticket.assigned_to_person_id = coerce_uuid(assignee)
        db.commit()
        db.refresh(ticket)
        return AssignmentResult(
            assigned=True,
            ticket_id=str(ticket.id),
            rule_id=str(rule.id),
            rule_name=rule.name,
            strategy=rule.strategy.value if rule.strategy else None,
            candidate_count=candidate_count,
            assignee_person_id=assignee,
            reason="assigned",
        )

    if last_matched_rule is not None:
        return AssignmentResult(
            assigned=False,
            ticket_id=str(ticket.id),
            rule_id=str(last_matched_rule.id),
            rule_name=last_matched_rule.name,
            strategy=last_matched_rule.strategy.value if last_matched_rule.strategy else None,
            candidate_count=last_candidate_count,
            reason="no_eligible_candidates",
        )
    return AssignmentResult(assigned=False, ticket_id=str(ticket.id), reason="no_matching_rule")


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
