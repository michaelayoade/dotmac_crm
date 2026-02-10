"""Resource ownership and access control.

Provides ownership validation for single-tenant deployments where:
- Users can view/edit resources they created
- Users can view/edit resources assigned to them
- Admins bypass all ownership checks

Usage:
    from app.services.ownership import OwnershipChecker, require_ownership

    # In service layer
    checker = OwnershipChecker(current_user_id, roles=["staff"])
    if not checker.can_access(ticket):
        raise ForbiddenError()

    # As decorator for service methods
    @require_ownership("ticket")
    def update_ticket(self, db, ticket_id, payload, current_user):
        ...
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, TypeVar
from uuid import UUID

from fastapi import HTTPException

if TYPE_CHECKING:
    pass


class OwnedResource(Protocol):
    """Protocol for resources that support ownership checks."""

    id: UUID
    created_by_person_id: UUID | None
    assigned_to_person_id: UUID | None


T = TypeVar("T", bound=OwnedResource)


class OwnershipChecker:
    """Check resource ownership for access control.

    Supports multiple ownership patterns:
    - Creator: created_by_person_id matches user
    - Assignee: assigned_to_person_id matches user
    - Admin bypass: users with 'admin' role skip all checks

    Attributes:
        user_id: Current user's person ID
        roles: User's roles (admin role bypasses checks)
        check_creator: Whether to check created_by_person_id
        check_assignee: Whether to check assigned_to_person_id
    """

    ADMIN_ROLES: ClassVar[set[str]] = {"admin", "superadmin"}

    def __init__(
        self,
        user_id: UUID | str | None,
        roles: list[str] | None = None,
        check_creator: bool = True,
        check_assignee: bool = True,
    ):
        from app.services.common import coerce_uuid

        self.user_id = coerce_uuid(user_id) if user_id else None
        self.roles = set(roles or [])
        self.check_creator = check_creator
        self.check_assignee = check_assignee

    @property
    def is_admin(self) -> bool:
        """Check if user has admin role."""
        return bool(self.roles & self.ADMIN_ROLES)

    def can_access(self, resource: OwnedResource) -> bool:
        """Check if user can access (view) the resource.

        Returns True if any of:
        - User is admin
        - User created the resource
        - User is assigned to the resource
        """
        if self.is_admin:
            return True

        if not self.user_id:
            return False

        if self.check_creator:
            created_by = getattr(resource, "created_by_person_id", None)
            if created_by and created_by == self.user_id:
                return True

        if self.check_assignee:
            assigned_to = getattr(resource, "assigned_to_person_id", None)
            if assigned_to and assigned_to == self.user_id:
                return True

        return False

    def can_modify(self, resource: OwnedResource) -> bool:
        """Check if user can modify (update/delete) the resource.

        Same rules as can_access for single-tenant.
        Override in subclass for different modify rules.
        """
        return self.can_access(resource)

    def can_delete(self, resource: OwnedResource) -> bool:
        """Check if user can delete the resource.

        By default, only admins and creators can delete.
        """
        if self.is_admin:
            return True

        if not self.user_id:
            return False

        created_by = getattr(resource, "created_by_person_id", None)
        return created_by is not None and created_by == self.user_id

    def filter_accessible(self, resources: list[T]) -> list[T]:
        """Filter list to only resources user can access."""
        if self.is_admin:
            return resources
        return [r for r in resources if self.can_access(r)]

    def assert_access(self, resource: OwnedResource, action: str = "access") -> None:
        """Raise HTTPException if user cannot access resource."""
        if not self.can_access(resource):
            raise HTTPException(
                status_code=403,
                detail=f"You do not have permission to {action} this resource",
            )

    def assert_modify(self, resource: OwnedResource) -> None:
        """Raise HTTPException if user cannot modify resource."""
        if not self.can_modify(resource):
            raise HTTPException(
                status_code=403,
                detail="You do not have permission to modify this resource",
            )

    def assert_delete(self, resource: OwnedResource) -> None:
        """Raise HTTPException if user cannot delete resource."""
        if not self.can_delete(resource):
            raise HTTPException(
                status_code=403,
                detail="You do not have permission to delete this resource",
            )


def get_ownership_checker(auth: dict) -> OwnershipChecker:
    """Create OwnershipChecker from auth context.

    Args:
        auth: Auth dict from require_user_auth dependency
              Contains person_id, roles, scopes

    Returns:
        Configured OwnershipChecker
    """
    return OwnershipChecker(
        user_id=auth.get("person_id"),
        roles=auth.get("roles", []),
    )


def require_ownership(
    resource_param: str = "resource",
    action: str = "access",
    allow_unassigned: bool = False,
):
    """Decorator to require resource ownership for service methods.

    Args:
        resource_param: Name of parameter containing the resource
        action: Action name for error messages
        allow_unassigned: If True, allow access to unassigned resources

    Usage:
        class TicketService:
            @require_ownership("ticket", action="update")
            def update(self, db, ticket, payload, auth):
                ...

    The decorated function must accept an 'auth' keyword argument
    containing the auth context from require_user_auth.
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            auth = kwargs.get("auth")
            if not auth:
                raise HTTPException(
                    status_code=401,
                    detail="Authentication required",
                )

            resource = kwargs.get(resource_param)
            if resource is None:
                # Resource not provided, let the function handle it
                return func(*args, **kwargs)

            checker = get_ownership_checker(auth)

            # Allow unassigned resources if configured
            if allow_unassigned:
                assigned_to = getattr(resource, "assigned_to_person_id", None)
                if assigned_to is None:
                    return func(*args, **kwargs)

            checker.assert_access(resource, action=action)
            return func(*args, **kwargs)

        return wrapper

    return decorator


# Convenience functions for common checks


def check_ticket_access(
    ticket: Any,
    user_id: UUID | str | None,
    roles: list[str] | None = None,
) -> bool:
    """Check if user can access a ticket."""
    checker = OwnershipChecker(user_id, roles)
    return checker.can_access(ticket)


def check_work_order_access(
    work_order: Any,
    user_id: UUID | str | None,
    roles: list[str] | None = None,
) -> bool:
    """Check if user can access a work order."""
    checker = OwnershipChecker(user_id, roles)
    return checker.can_access(work_order)


def check_project_access(
    project: Any,
    user_id: UUID | str | None,
    roles: list[str] | None = None,
) -> bool:
    """Check if user can access a project.

    Projects have owner_person_id instead of assigned_to_person_id.
    """
    checker = OwnershipChecker(user_id, roles, check_assignee=False)

    if checker.is_admin:
        return True

    if not checker.user_id:
        return False

    # Check creator
    created_by = getattr(project, "created_by_person_id", None)
    if created_by and created_by == checker.user_id:
        return True

    # Check owner
    owner = getattr(project, "owner_person_id", None)
    if owner and owner == checker.user_id:
        return True

    # Check manager
    manager = getattr(project, "manager_person_id", None)
    return bool(manager and manager == checker.user_id)
