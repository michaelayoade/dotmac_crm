"""Ticket provider for the Workqueue.

The `Ticket` model has no ``sla_due_at`` or ``last_customer_reply_at``
columns; per the implementation plan we derive them from ``Ticket.metadata_``
(JSON) — the same pattern used by the conversations provider.  Assignment to
the current user is resolved via the ``TicketAssignee`` join table, which
references ``people.id`` directly through ``person_id``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.tickets import Ticket, TicketPriority, TicketStatus
from app.services.workqueue.providers import register
from app.services.workqueue.scope import WorkqueueScope, apply_ticket_scope
from app.services.workqueue.scoring_config import (
    PROVIDER_LIMIT,
    TICKET_SCORES,
    TICKET_SLA_IMMINENT_SEC,
    TICKET_SLA_SOON_SEC,
)
from app.services.workqueue.types import (
    ActionKind,
    ItemKind,
    WorkqueueAudience,
    WorkqueueItem,
    urgency_for_score,
)

_OPEN_STATUSES = (
    TicketStatus.new,
    TicketStatus.open,
    TicketStatus.pending,
    TicketStatus.waiting_on_customer,
)
logger = logging.getLogger(__name__)


def _parse_dt(value) -> datetime | None:
    """Parse a datetime stored in JSON metadata.

    Accepts ``datetime`` instances or ISO-8601 strings.  Returns timezone-aware
    UTC datetimes; naive values are assumed to be UTC.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _meta(t: Ticket) -> dict:
    return t.metadata_ or {}


def _sla_due_at(t: Ticket) -> datetime | None:
    return _parse_dt(_meta(t).get("sla_due_at"))


def _last_customer_reply_at(t: Ticket) -> datetime | None:
    return _parse_dt(_meta(t).get("last_customer_reply_at"))


def _due_at(t: Ticket) -> datetime | None:
    due = getattr(t, "due_at", None)
    if due is None:
        return None
    return due if due.tzinfo else due.replace(tzinfo=UTC)


def _classify(t: Ticket, now: datetime) -> tuple[str, int] | None:
    sla_due = _sla_due_at(t)
    if sla_due is not None:
        delta = (sla_due - now).total_seconds()
        if delta <= 0:
            return "sla_breach", TICKET_SCORES["sla_breach"]
        if delta <= TICKET_SLA_IMMINENT_SEC:
            return "sla_imminent", TICKET_SCORES["sla_imminent"]
        if delta <= TICKET_SLA_SOON_SEC:
            return "sla_soon", TICKET_SCORES["sla_soon"]

    if t.priority == TicketPriority.urgent and t.status in _OPEN_STATUSES:
        return "priority_urgent", TICKET_SCORES["priority_urgent"]

    due = _due_at(t)
    if due is not None and due < now:
        return "overdue", TICKET_SCORES["overdue"]

    if t.status == TicketStatus.waiting_on_customer and _last_customer_reply_at(t):
        return "customer_replied", TICKET_SCORES["customer_replied"]

    return None


def _active_assignee_person_id(t: Ticket) -> UUID | None:
    """Return the person id of the first assignee, if any."""
    assignees = list(getattr(t, "assignees", None) or ())
    if assignees:
        return assignees[0].person_id
    return getattr(t, "assigned_to_person_id", None)


def _visibility_source(t: Ticket, scope: WorkqueueScope) -> str:
    assignee = _active_assignee_person_id(t)
    if assignee == scope.person_id:
        return "direct_assignment"
    if t.service_team_id is not None and t.service_team_id in scope.accessible_service_team_ids:
        return "service_team_ownership"
    return "unknown"


def _title(t: Ticket) -> str:
    number = getattr(t, "number", None)
    prefix = f"T-{number}" if number else f"T-{t.id}"
    return f"{prefix} · {t.title}"


def _subtitle(reason: str, t: Ticket, now: datetime) -> str:
    sla_due = _sla_due_at(t)
    if reason == "sla_breach" and sla_due:
        secs = int((now - sla_due).total_seconds())
        return f"SLA breached {secs // 60}m ago"
    if reason in ("sla_imminent", "sla_soon") and sla_due:
        secs = int((sla_due - now).total_seconds())
        return f"SLA in {secs // 60}m"
    if reason == "priority_urgent":
        return "Urgent priority"
    if reason == "overdue":
        due = _due_at(t)
        if due is not None:
            secs = int((now - due).total_seconds())
            return f"Overdue by {secs // 3600}h"
        return "Overdue"
    if reason == "customer_replied":
        return "Customer replied"
    return reason.replace("_", " ").title()


class TicketsProvider:
    """Workqueue provider that surfaces actionable tickets."""

    kind = ItemKind.ticket

    def fetch(
        self,
        db: Session,
        *,
        user,
        audience: WorkqueueAudience,
        scope: WorkqueueScope,
        snoozed_ids: set[UUID],
        limit: int = PROVIDER_LIMIT,
    ) -> list[WorkqueueItem]:
        now = datetime.now(UTC)

        stmt = (
            select(Ticket)
            .options(selectinload(Ticket.assignees))
            .where(Ticket.status.in_(_OPEN_STATUSES))
            .where(Ticket.is_active.is_(True))
        )
        stmt = apply_ticket_scope(stmt, scope)

        if snoozed_ids:
            stmt = stmt.where(~Ticket.id.in_(snoozed_ids))

        stmt = stmt.limit(limit * 2)
        rows = db.execute(stmt).scalars().unique().all()

        items: list[WorkqueueItem] = []
        for t in rows:
            verdict = _classify(t, now)
            if verdict is None:
                continue
            reason, score = verdict
            assignee = _active_assignee_person_id(t)
            actions = {ActionKind.open, ActionKind.snooze, ActionKind.complete}
            if assignee is None:
                actions.add(ActionKind.claim)
            visibility_source = _visibility_source(t, scope)
            logger.info(
                "workqueue_item_included kind=ticket user_id=%s item_id=%s visibility_source=%s assignee_source=%s team_source=%s",
                scope.person_id,
                t.id,
                visibility_source,
                assignee,
                t.service_team_id,
            )
            items.append(
                WorkqueueItem(
                    kind=ItemKind.ticket,
                    item_id=t.id,
                    title=_title(t),
                    subtitle=_subtitle(reason, t, now),
                    score=score,
                    reason=reason,
                    urgency=urgency_for_score(score),
                    deep_link=f"/admin/tickets/{t.id}",
                    assignee_id=assignee,
                    is_unassigned=assignee is None,
                    happened_at=t.updated_at or now,
                    actions=frozenset(actions),
                    metadata={
                        "priority": getattr(t.priority, "value", None) if t.priority is not None else None,
                        "visibility_source": visibility_source,
                    },
                )
            )

        items.sort(key=lambda i: -i.score)
        return items[:limit]


tickets_provider = register(TicketsProvider())
