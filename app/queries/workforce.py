"""Query builders for workforce-related models."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from app.models.workforce import (
    WorkOrder,
    WorkOrderAssignment,
    WorkOrderNote,
    WorkOrderPriority,
    WorkOrderStatus,
    WorkOrderType,
)
from app.queries.base import BaseQuery
from app.services.common import coerce_uuid, validate_enum

if TYPE_CHECKING:
    from uuid import UUID


class WorkOrderQuery(BaseQuery[WorkOrder]):
    """Query builder for WorkOrder model.

    Usage:
        work_orders = (
            WorkOrderQuery(db)
            .by_subscriber(subscriber_id)
            .by_status(WorkOrderStatus.scheduled)
            .by_assigned_to(technician_id)
            .active_only()
            .order_by("scheduled_start", "asc")
            .paginate(50, 0)
            .all()
        )
    """

    model_class = WorkOrder
    ordering_fields: ClassVar[dict[str, Any]] = {
        "created_at": WorkOrder.created_at,
        "updated_at": WorkOrder.updated_at,
        "status": WorkOrder.status,
        "priority": WorkOrder.priority,
        "scheduled_start": WorkOrder.scheduled_start,
        "scheduled_end": WorkOrder.scheduled_end,
    }

    def by_subscriber(self, subscriber_id: UUID | str | None) -> WorkOrderQuery:
        """Filter by subscriber ID."""
        if not subscriber_id:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(WorkOrder.subscriber_id == coerce_uuid(subscriber_id))
        return clone

    def by_ticket(self, ticket_id: UUID | str | None) -> WorkOrderQuery:
        """Filter by linked ticket ID."""
        if not ticket_id:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(WorkOrder.ticket_id == coerce_uuid(ticket_id))
        return clone

    def by_project(self, project_id: UUID | str | None) -> WorkOrderQuery:
        """Filter by linked project ID."""
        if not project_id:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(WorkOrder.project_id == coerce_uuid(project_id))
        return clone

    def by_assigned_to(self, person_id: UUID | str | None) -> WorkOrderQuery:
        """Filter by assigned person ID."""
        if not person_id:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(WorkOrder.assigned_to_person_id == coerce_uuid(person_id))
        return clone

    def unassigned(self) -> WorkOrderQuery:
        """Filter to only unassigned work orders."""
        clone = self._clone()
        clone._query = clone._query.filter(WorkOrder.assigned_to_person_id.is_(None))
        return clone

    def by_status(self, status: WorkOrderStatus | str | None) -> WorkOrderQuery:
        """Filter by work order status."""
        if not status:
            return self
        clone = self._clone()
        if isinstance(status, str):
            status = validate_enum(status, WorkOrderStatus, "status")
        clone._query = clone._query.filter(WorkOrder.status == status)
        return clone

    def by_statuses(self, statuses: list[WorkOrderStatus | str]) -> WorkOrderQuery:
        """Filter by multiple statuses."""
        if not statuses:
            return self
        clone = self._clone()
        status_enums = [validate_enum(s, WorkOrderStatus, "status") if isinstance(s, str) else s for s in statuses]
        clone._query = clone._query.filter(WorkOrder.status.in_(status_enums))
        return clone

    def by_priority(self, priority: WorkOrderPriority | str | None) -> WorkOrderQuery:
        """Filter by work order priority."""
        if not priority:
            return self
        clone = self._clone()
        if isinstance(priority, str):
            priority = validate_enum(priority, WorkOrderPriority, "priority")
        clone._query = clone._query.filter(WorkOrder.priority == priority)
        return clone

    def by_work_type(self, work_type: WorkOrderType | str | None) -> WorkOrderQuery:
        """Filter by work order type."""
        if not work_type:
            return self
        clone = self._clone()
        if isinstance(work_type, str):
            work_type = validate_enum(work_type, WorkOrderType, "work_type")
        clone._query = clone._query.filter(WorkOrder.work_type == work_type)
        return clone

    def pending(self) -> WorkOrderQuery:
        """Filter to pending work orders (draft, scheduled, dispatched)."""
        return self.by_statuses(
            [
                WorkOrderStatus.draft,
                WorkOrderStatus.scheduled,
                WorkOrderStatus.dispatched,
            ]
        )

    def in_progress(self) -> WorkOrderQuery:
        """Filter to in-progress work orders."""
        return self.by_status(WorkOrderStatus.in_progress)

    def completed(self) -> WorkOrderQuery:
        """Filter to completed work orders."""
        return self.by_statuses(
            [
                WorkOrderStatus.completed,
            ]
        )


class WorkOrderAssignmentQuery(BaseQuery[WorkOrderAssignment]):
    """Query builder for WorkOrderAssignment model."""

    model_class = WorkOrderAssignment
    ordering_fields: ClassVar[dict[str, Any]] = {
        "assigned_at": WorkOrderAssignment.assigned_at,
    }

    def by_work_order(self, work_order_id: UUID | str | None) -> WorkOrderAssignmentQuery:
        """Filter by work order ID."""
        if not work_order_id:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(WorkOrderAssignment.work_order_id == coerce_uuid(work_order_id))
        return clone

    def by_person(self, person_id: UUID | str | None) -> WorkOrderAssignmentQuery:
        """Filter by assigned person ID."""
        if not person_id:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(WorkOrderAssignment.person_id == coerce_uuid(person_id))
        return clone

    def primary_only(self) -> WorkOrderAssignmentQuery:
        """Filter to only primary assignments."""
        clone = self._clone()
        clone._query = clone._query.filter(WorkOrderAssignment.is_primary.is_(True))
        return clone


class WorkOrderNoteQuery(BaseQuery[WorkOrderNote]):
    """Query builder for WorkOrderNote model."""

    model_class = WorkOrderNote
    ordering_fields: ClassVar[dict[str, Any]] = {
        "created_at": WorkOrderNote.created_at,
    }

    def by_work_order(self, work_order_id: UUID | str | None) -> WorkOrderNoteQuery:
        """Filter by work order ID."""
        if not work_order_id:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(WorkOrderNote.work_order_id == coerce_uuid(work_order_id))
        return clone

    def by_author(self, person_id: UUID | str | None) -> WorkOrderNoteQuery:
        """Filter by author person ID."""
        if not person_id:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(WorkOrderNote.author_person_id == coerce_uuid(person_id))
        return clone

    def is_internal(self, internal: bool | None) -> WorkOrderNoteQuery:
        """Filter by internal flag."""
        if internal is None:
            return self
        clone = self._clone()
        clone._query = clone._query.filter(WorkOrderNote.is_internal == internal)
        return clone
