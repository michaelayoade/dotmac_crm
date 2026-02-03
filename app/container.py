"""Dependency injection container.

This module provides a centralized container for managing service dependencies,
enabling proper testing through dependency mocking and ensuring explicit
dependency graphs.

Usage:
    from app.container import Container

    # In FastAPI startup
    container = Container()
    container.config.from_dict(settings)

    # In route handlers
    @router.post("/tickets")
    @inject
    def create_ticket(
        payload: TicketCreate,
        service: TicketService = Depends(Provide[Container.ticket_service]),
    ):
        return service.create(payload)

    # In tests
    with container.ticket_service.override(MockTicketService()):
        response = client.post("/tickets", ...)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dependency_injector import containers, providers  # type: ignore[import-not-found]

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _ticket_query_factory(db: "Session"):
    from app.queries.tickets import TicketQuery
    return TicketQuery(db)


def _work_order_query_factory(db: "Session"):
    from app.queries.workforce import WorkOrderQuery
    return WorkOrderQuery(db)


def _project_query_factory(db: "Session"):
    from app.queries.projects import ProjectQuery
    return ProjectQuery(db)


def _get_ticket_service():
    from app.services.tickets import tickets
    return tickets


def _get_ticket_comments_service():
    from app.services.tickets import ticket_comments
    return ticket_comments


def _get_work_orders_service():
    from app.services.workforce import work_orders
    return work_orders


def _get_projects_service():
    from app.services.projects import projects
    return projects


def _get_project_tasks_service():
    from app.services.projects import project_tasks
    return project_tasks


class Container(containers.DeclarativeContainer):
    """Application dependency injection container.

    Provides:
    - Configuration management
    - Database session factory
    - Service instances with dependencies
    - Query builder factories

    Services are provided as Factory providers, creating new instances
    per request with injected dependencies.
    """

    # Wiring configuration - modules that can use @inject decorator
    wiring_config = containers.WiringConfiguration(
        modules=[
            "app.api.tickets",
            "app.api.workforce",
            "app.api.projects",
        ]
    )

    # Configuration provider
    config = providers.Configuration()

    # Database session factory
    # This is typically overridden at runtime with the actual SessionLocal
    db_session_factory = providers.Callable(
        lambda: None  # Placeholder, configured at runtime
    )

    # -------------------------------------------------------------------------
    # Query Builder Factories
    # -------------------------------------------------------------------------
    # These create query builders with injected database sessions

    ticket_query = providers.Factory(_ticket_query_factory)
    work_order_query = providers.Factory(_work_order_query_factory)
    project_query = providers.Factory(_project_query_factory)

    # -------------------------------------------------------------------------
    # Service Providers
    # -------------------------------------------------------------------------
    # Services are provided as singletons since they're stateless managers

    # Service providers - return existing singleton instances
    ticket_service = providers.Singleton(_get_ticket_service)
    ticket_comments_service = providers.Singleton(_get_ticket_comments_service)
    work_orders_service = providers.Singleton(_get_work_orders_service)
    projects_service = providers.Singleton(_get_projects_service)
    project_tasks_service = providers.Singleton(_get_project_tasks_service)


# Global container instance
container = Container()


def get_container() -> Container:
    """Get the global container instance."""
    return container


def configure_container(db_session_factory) -> Container:
    """Configure the container with runtime dependencies.

    Args:
        db_session_factory: Callable that returns a new database session

    Returns:
        Configured container instance
    """
    container.db_session_factory.override(providers.Callable(db_session_factory))
    return container
