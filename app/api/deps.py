from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.auth_dependencies import (
    require_audit_auth,
    require_permission,
    require_role,
    require_user_auth,
)


def get_current_user(auth=Depends(require_user_auth)):
    """Get current authenticated user info.

    Returns a dict with person_id, session_id, roles, and scopes.
    """
    return auth


# -------------------------------------------------------------------------
# Container-based Dependencies
# -------------------------------------------------------------------------
# These provide services from the DI container for use in route handlers.
# They can be easily mocked in tests by overriding the container providers.


def get_ticket_service():
    """Get ticket service from container."""
    from app.container import container
    return container.ticket_service()


def get_ticket_comments_service():
    """Get ticket comments service from container."""
    from app.container import container
    return container.ticket_comments_service()


def get_work_orders_service():
    """Get work orders service from container."""
    from app.container import container
    return container.work_orders_service()


def get_projects_service():
    """Get projects service from container."""
    from app.container import container
    return container.projects_service()


def get_project_tasks_service():
    """Get project tasks service from container."""
    from app.container import container
    return container.project_tasks_service()


# Query builder factories
def get_ticket_query(db: Session = Depends(get_db)):
    """Get a TicketQuery builder with injected session."""
    from app.queries.tickets import TicketQuery
    return TicketQuery(db)


def get_work_order_query(db: Session = Depends(get_db)):
    """Get a WorkOrderQuery builder with injected session."""
    from app.queries.workforce import WorkOrderQuery
    return WorkOrderQuery(db)


def get_project_query(db: Session = Depends(get_db)):
    """Get a ProjectQuery builder with injected session."""
    from app.queries.projects import ProjectQuery
    return ProjectQuery(db)


# -------------------------------------------------------------------------
# Ownership Checking
# -------------------------------------------------------------------------


def get_ownership_checker(auth=Depends(require_user_auth)):
    """Get ownership checker from auth context.

    Usage:
        @router.put("/tickets/{id}")
        def update_ticket(
            id: str,
            payload: TicketUpdate,
            checker: OwnershipChecker = Depends(get_ownership_checker),
            db: Session = Depends(get_db),
        ):
            ticket = tickets.get(db, id)
            checker.assert_modify(ticket)
            return tickets.update(db, id, payload)
    """
    from app.services.ownership import OwnershipChecker

    return OwnershipChecker(
        user_id=auth.get("person_id"),
        roles=auth.get("roles", []),
    )


__all__ = [
    "get_db",
    "get_current_user",
    "require_audit_auth",
    "require_permission",
    "require_role",
    "require_user_auth",
    # Container-based dependencies
    "get_ticket_service",
    "get_ticket_comments_service",
    "get_work_orders_service",
    "get_projects_service",
    "get_project_tasks_service",
    # Query builders
    "get_ticket_query",
    "get_work_order_query",
    "get_project_query",
    # Ownership checking
    "get_ownership_checker",
]
