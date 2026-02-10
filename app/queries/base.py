"""Base query builder class.

Provides common query operations that all query builders inherit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Generic, Self, TypeVar

from sqlalchemy import asc, desc
from sqlalchemy.orm import Query

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session

T = TypeVar("T")


class BaseQuery(Generic[T]):
    """Base class for composable query builders.

    Provides fluent interface for building SQLAlchemy queries with:
    - Chainable filter methods
    - Ordering support
    - Pagination
    - Count operations

    Subclasses should:
    1. Set `model_class` to the SQLAlchemy model
    2. Define `ordering_fields` mapping column names to model attributes
    3. Implement domain-specific filter methods
    """

    model_class: type[T]
    ordering_fields: ClassVar[dict[str, Any]] = {}

    def __init__(self, db: Session):
        self.db = db
        self._query: Query = db.query(self.model_class)

    def _clone(self) -> Self:
        """Create a copy of this query builder with current state."""
        new = self.__class__.__new__(self.__class__)
        new.db = self.db
        new._query = self._query
        return new

    # -------------------------------------------------------------------------
    # Common filters
    # -------------------------------------------------------------------------

    def by_id(self, id: UUID | str) -> Self:
        """Filter by primary key ID."""
        from app.services.common import coerce_uuid

        clone = self._clone()
        id_column = getattr(self.model_class, "id", None)
        if id_column is not None:
            clone._query = clone._query.filter(id_column == coerce_uuid(id))
        return clone

    def by_ids(self, ids: list[UUID | str]) -> Self:
        """Filter by multiple IDs."""
        from app.services.common import coerce_uuid

        clone = self._clone()
        id_column = getattr(self.model_class, "id", None)
        if id_column is not None and ids:
            uuid_ids = [coerce_uuid(i) for i in ids]
            clone._query = clone._query.filter(id_column.in_(uuid_ids))
        return clone

    def active_only(self, active: bool = True) -> Self:
        """Filter by is_active flag."""
        clone = self._clone()
        is_active_col = getattr(self.model_class, "is_active", None)
        if is_active_col is not None:
            if active:
                clone._query = clone._query.filter(is_active_col.is_(True))
            else:
                clone._query = clone._query.filter(is_active_col.is_(False))
        return clone

    def include_inactive(self) -> Self:
        """Don't filter by is_active (include all records)."""
        # This is a no-op since we don't filter by default
        # But it makes the intent explicit when chaining
        return self._clone()

    # -------------------------------------------------------------------------
    # Ordering
    # -------------------------------------------------------------------------

    def order_by(self, field: str, direction: str = "asc") -> Self:
        """Apply ordering to the query.

        Args:
            field: Field name (must be in ordering_fields)
            direction: 'asc' or 'desc'
        """
        clone = self._clone()
        column = self.ordering_fields.get(field)
        if column is not None:
            if direction.lower() == "desc":
                clone._query = clone._query.order_by(desc(column))
            else:
                clone._query = clone._query.order_by(asc(column))
        return clone

    # -------------------------------------------------------------------------
    # Pagination
    # -------------------------------------------------------------------------

    def paginate(self, limit: int = 50, offset: int = 0) -> Self:
        """Apply pagination to the query."""
        clone = self._clone()
        if limit > 0:
            clone._query = clone._query.limit(limit)
        if offset > 0:
            clone._query = clone._query.offset(offset)
        return clone

    # -------------------------------------------------------------------------
    # Execution
    # -------------------------------------------------------------------------

    def all(self) -> list[T]:
        """Execute query and return all results."""
        return self._query.all()

    def first(self) -> T | None:
        """Execute query and return first result."""
        return self._query.first()

    def one(self) -> T:
        """Execute query and return exactly one result (raises if not found)."""
        return self._query.one()

    def one_or_none(self) -> T | None:
        """Execute query and return one result or None."""
        return self._query.one_or_none()

    def count(self) -> int:
        """Return count of matching records."""
        return self._query.count()

    def exists(self) -> bool:
        """Check if any matching records exist."""
        return self.db.query(self._query.exists()).scalar()

    def query(self) -> Query:
        """Return the underlying SQLAlchemy Query object."""
        return self._query
