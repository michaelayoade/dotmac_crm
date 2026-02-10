"""Query builders for ticket-related models."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import String, cast, or_
from sqlalchemy.orm import selectinload

from app.models.tickets import (
    Ticket,
    TicketAssignee,
    TicketChannel,
    TicketComment,
    TicketPriority,
    TicketSlaEvent,
    TicketStatus,
)
from app.queries.base import BaseQuery
from app.services.common import coerce_uuid, validate_enum

if TYPE_CHECKING:
    from uuid import UUID


class TicketQuery(BaseQuery[Ticket]):
    """Query builder for Ticket model.

    Usage:
        tickets = (
            TicketQuery(db)
            .by_subscriber(subscriber_id)
            .by_status(TicketStatus.open)
            .search("network")
            .active_only()
            .order_by("created_at", "desc")
            .paginate(50, 0)
            .all()
        )
    """

    model_class = Ticket
    ordering_fields: ClassVar[dict[str, Any]] = {
        "created_at": Ticket.created_at,
        "updated_at": Ticket.updated_at,
        "status": Ticket.status,
        "priority": Ticket.priority,
    }

    def by_subscriber(self, subscriber_id: UUID | str | None) -> TicketQuery:
        """Filter by subscriber ID."""
        if not subscriber_id:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(
            Ticket.subscriber_id == coerce_uuid(subscriber_id)
        )
        return clone

    def by_status(self, status: TicketStatus | str | None) -> TicketQuery:
        """Filter by ticket status."""
        if not status:
            return self
        clone = self._clone()
        if isinstance(status, str):
            status = validate_enum(status, TicketStatus, "status")
        clone._query = clone._query.filter(Ticket.status == status)
        return clone

    def by_statuses(self, statuses: list[TicketStatus | str]) -> TicketQuery:
        """Filter by multiple statuses."""
        if not statuses:
            return self
        clone = self._clone()
        status_enums = [
            validate_enum(s, TicketStatus, "status") if isinstance(s, str) else s
            for s in statuses
        ]
        clone._query = clone._query.filter(Ticket.status.in_(status_enums))
        return clone

    def by_priority(self, priority: TicketPriority | str | None) -> TicketQuery:
        """Filter by ticket priority."""
        if not priority:
            return self
        clone = self._clone()
        if isinstance(priority, str):
            priority = validate_enum(priority, TicketPriority, "priority")
        clone._query = clone._query.filter(Ticket.priority == priority)
        return clone

    def by_channel(self, channel: TicketChannel | str | None) -> TicketQuery:
        """Filter by ticket channel."""
        if not channel:
            return self
        clone = self._clone()
        if isinstance(channel, str):
            channel = validate_enum(channel, TicketChannel, "channel")
        clone._query = clone._query.filter(Ticket.channel == channel)
        return clone

    def by_created_by(self, person_id: UUID | str | None) -> TicketQuery:
        """Filter by creator person ID."""
        if not person_id:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(
            Ticket.created_by_person_id == coerce_uuid(person_id)
        )
        return clone

    def by_assigned_to(self, person_id: UUID | str | None) -> TicketQuery:
        """Filter by assigned person ID."""
        if not person_id:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(
            Ticket.assigned_to_person_id == coerce_uuid(person_id)
        )
        return clone

    def unassigned(self) -> TicketQuery:
        """Filter to only unassigned tickets."""
        clone = self._clone()
        clone._query = clone._query.filter(Ticket.assigned_to_person_id.is_(None))
        return clone

    def search(self, term: str | None) -> TicketQuery:
        """Search tickets by title, description, or ID.

        Performs case-insensitive search across multiple fields.
        """
        if not term or not term.strip():
            return self
        clone = self._clone()
        like_term = f"%{term.strip()}%"
        search_filters = [
            Ticket.title.ilike(like_term),
            Ticket.description.ilike(like_term),
            cast(Ticket.id, String).ilike(like_term),
        ]
        ticket_number_attr = getattr(Ticket, "number", None)
        if ticket_number_attr is not None:
            search_filters.append(ticket_number_attr.ilike(like_term))
        clone._query = clone._query.filter(or_(*search_filters))
        return clone

    def with_tag(self, tag: str) -> TicketQuery:
        """Filter tickets that have a specific tag."""
        clone = self._clone()
        # Assumes tags is a JSON array column
        clone._query = clone._query.filter(Ticket.tags.contains([tag]))
        return clone

    def open_tickets(self) -> TicketQuery:
        """Filter to only open tickets (not resolved/closed)."""
        return self.by_statuses([
            TicketStatus.new,
            TicketStatus.open,
            TicketStatus.pending,
            TicketStatus.on_hold,
        ])

    def closed_tickets(self) -> TicketQuery:
        """Filter to only closed tickets."""
        return self.by_statuses([
            TicketStatus.resolved,
            TicketStatus.closed,
            TicketStatus.canceled,
        ])

    def with_relations(self) -> TicketQuery:
        """Eager load common relationships to avoid N+1 queries.

        Loads: subscriber, customer, created_by, assigned_to, lead.
        """
        clone = self._clone()
        clone._query = clone._query.options(
            selectinload(Ticket.subscriber),
            selectinload(Ticket.customer),
            selectinload(Ticket.created_by),
            selectinload(Ticket.assigned_to),
            selectinload(Ticket.assignees).selectinload(TicketAssignee.person),
            selectinload(Ticket.ticket_manager),
            selectinload(Ticket.assistant_manager),
            selectinload(Ticket.lead),
        )
        return clone


class TicketCommentQuery(BaseQuery[TicketComment]):
    """Query builder for TicketComment model."""

    model_class = TicketComment
    ordering_fields: ClassVar[dict[str, Any]] = {
        "created_at": TicketComment.created_at,
    }

    def by_ticket(self, ticket_id: UUID | str | None) -> TicketCommentQuery:
        """Filter by ticket ID."""
        if not ticket_id:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(
            TicketComment.ticket_id == coerce_uuid(ticket_id)
        )
        return clone

    def by_author(self, person_id: UUID | str | None) -> TicketCommentQuery:
        """Filter by author person ID."""
        if not person_id:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(
            TicketComment.author_person_id == coerce_uuid(person_id)
        )
        return clone

    def internal_only(self) -> TicketCommentQuery:
        """Filter to only internal comments."""
        clone = self._clone()
        clone._query = clone._query.filter(TicketComment.is_internal.is_(True))
        return clone

    def external_only(self) -> TicketCommentQuery:
        """Filter to only external (customer-visible) comments."""
        clone = self._clone()
        clone._query = clone._query.filter(TicketComment.is_internal.is_(False))
        return clone

    def is_internal(self, internal: bool | None) -> TicketCommentQuery:
        """Filter by internal flag."""
        if internal is None:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(TicketComment.is_internal == internal)
        return clone

    def with_author(self) -> TicketCommentQuery:
        """Eager load author relationship to avoid N+1 queries."""
        clone = self._clone()
        clone._query = clone._query.options(selectinload(TicketComment.author))
        return clone


class TicketSlaEventQuery(BaseQuery[TicketSlaEvent]):
    """Query builder for TicketSlaEvent model."""

    model_class = TicketSlaEvent
    ordering_fields: ClassVar[dict[str, Any]] = {
        "created_at": TicketSlaEvent.created_at,
        "event_type": TicketSlaEvent.event_type,
    }

    def by_ticket(self, ticket_id: UUID | str | None) -> TicketSlaEventQuery:
        """Filter by ticket ID."""
        if not ticket_id:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(
            TicketSlaEvent.ticket_id == coerce_uuid(ticket_id)
        )
        return clone

    def by_event_type(self, event_type: str | None) -> TicketSlaEventQuery:
        """Filter by event type."""
        if not event_type:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(TicketSlaEvent.event_type == event_type)
        return clone
