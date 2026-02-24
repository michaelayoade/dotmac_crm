---
name: add-service
description: Scaffold a new service module with model, service manager, schema, routes, and tests
arguments:
  - name: module_info
    description: "Module name and purpose (e.g. 'inventory reservations for stock management')"
---

# Add Service Module

Scaffold a complete service module for the DotMac Omni CRM.

## Steps

### 1. Understand the request
Parse `$ARGUMENTS` to determine:
- **Domain**: tickets, projects, crm, network, workforce, notifications, etc.
- **Entity name**: e.g. `Reservation`, `MaterialRequest`
- **Whether it needs**: web routes, API routes, or both

### 2. Study the closest existing pattern
Read these reference files to match conventions:
- **Model**: `app/models/tickets.py` — `Mapped[]` annotations, UUID PK, `is_active`, timestamps
- **Service**: `app/services/tickets.py` — singleton manager with `@staticmethod`, `ListResponseMixin`
- **Schema**: `app/schemas/tickets.py` — Pydantic v2 with `Base/Create/Update/Read` pattern
- **API route**: `app/api/tickets.py` — thin wrapper, permission checks
- **Web route**: `app/web/admin/tickets.py` — POST-Redirect-GET, `_base_ctx` helper

### 3. Create the model
Create `app/models/{module}.py`:

```python
import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class EntityStatus(enum.Enum):
    draft = "draft"
    active = "active"
    archived = "archived"


class Entity(Base):
    __tablename__ = "entities"
    __table_args__ = (
        Index("ix_entities_status", "status"),
        Index("ix_entities_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Add domain fields here
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[EntityStatus] = mapped_column(
        Enum(EntityStatus, name="entitystatus"), default=EntityStatus.draft, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
```

Register in `app/models/__init__.py`.

### 4. Create the schema
Create `app/schemas/{module}.py`:

```python
from __future__ import annotations
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EntityBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)

class EntityCreate(EntityBase):
    pass

class EntityUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    status: str | None = None

class EntityRead(EntityBase):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: UUID
    status: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
```

### 5. Create the service
Create `app/services/{module}.py`:

```python
import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.{module} import Entity, EntityStatus
from app.schemas.{module} import EntityCreate, EntityUpdate
from app.services.common import apply_ordering, apply_pagination, coerce_uuid, validate_enum
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


class Entities(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: EntityCreate) -> Entity:
        entity = Entity(**payload.model_dump())
        db.add(entity)
        db.commit()
        db.refresh(entity)
        return entity

    @staticmethod
    def get(db: Session, entity_id: str) -> Entity:
        entity = db.get(Entity, coerce_uuid(entity_id))
        if not entity or not entity.is_active:
            raise HTTPException(status_code=404, detail="Entity not found")
        return entity

    @staticmethod
    def list(
        db: Session,
        *,
        status: str | None = None,
        search: str | None = None,
        is_active: bool | None = None,
        order_by: str = "created_at",
        order_dir: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> list[Entity]:
        query = db.query(Entity)
        if status:
            query = query.filter(
                Entity.status == validate_enum(status, EntityStatus, "status")
            )
        if is_active is not None:
            query = query.filter(Entity.is_active == is_active)
        else:
            query = query.filter(Entity.is_active.is_(True))
        if search:
            query = query.filter(Entity.name.ilike(f"%{search}%"))
        allowed = {"created_at": Entity.created_at, "name": Entity.name}
        query = apply_ordering(query, order_by, order_dir, allowed)
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, entity_id: str, payload: EntityUpdate) -> Entity:
        entity = Entities.get(db, entity_id)
        updates = payload.model_dump(exclude_unset=True)
        if "status" in updates and updates["status"] is not None:
            updates["status"] = validate_enum(updates["status"], EntityStatus, "status")
        for key, value in updates.items():
            setattr(entity, key, value)
        db.commit()
        db.refresh(entity)
        return entity

    @staticmethod
    def delete(db: Session, entity_id: str) -> None:
        entity = Entities.get(db, entity_id)
        entity.is_active = False
        db.commit()


# Singleton instance
entities = Entities()
```

### 6. Create database migration
```bash
alembic revision --autogenerate -m "Add {module} table"
```
Review and make idempotent (see `add-migration` skill).

### 7. Create tests
Create `tests/test_{module}.py`:
- Test CRUD operations
- Test soft delete
- Test list filtering and pagination
- Test enum validation

### 8. Verify
```bash
ruff check app/models/{module}.py app/services/{module}.py app/schemas/{module}.py --fix
ruff format app/models/{module}.py app/services/{module}.py app/schemas/{module}.py
mypy app/models/{module}.py app/services/{module}.py
python -c "from app.models.{module} import Entity"
```
