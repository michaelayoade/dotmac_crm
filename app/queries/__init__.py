"""Query builders for database operations.

This module provides composable query builder classes that encapsulate
filter logic, making services cleaner and queries more testable.

Usage:
    from app.queries import TicketQuery

    results = (
        TicketQuery(db)
        .by_subscriber(subscriber_id)
        .by_status(TicketStatus.open)
        .search("network issue")
        .active_only()
        .order_by("created_at", "desc")
        .paginate(limit=50, offset=0)
        .all()
    )
"""

from app.queries.base import BaseQuery
from app.queries.tickets import TicketQuery, TicketCommentQuery
from app.queries.workforce import WorkOrderQuery, WorkOrderAssignmentQuery
from app.queries.projects import ProjectQuery, ProjectTaskQuery

__all__ = [
    "BaseQuery",
    "TicketQuery",
    "TicketCommentQuery",
    "WorkOrderQuery",
    "WorkOrderAssignmentQuery",
    "ProjectQuery",
    "ProjectTaskQuery",
]
