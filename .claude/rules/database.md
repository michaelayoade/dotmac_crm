# Database Rules

## Conventions

- **UUIDs** for all primary keys (`UUID(as_uuid=True)`)
- **Timestamps**: `created_at`, `updated_at` with `DateTime(timezone=True)`
- **Soft deletes**: `is_active` boolean (default True)
- **Relationships**: `back_populates` always
- **JSON columns**: `mapped_column(JSON)` for flexible metadata
- **Enum columns**: PostgreSQL ENUM with `create_type=False` + `checkfirst=True` in migrations

## Model Pattern (SQLAlchemy 2.0)

```python
class Entity(Base):
    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
        default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))
```

## Schema Pattern (Pydantic v2)

```python
class EntityBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)

class EntityCreate(EntityBase): pass

class EntityUpdate(BaseModel):  # Separate base — all optional
    name: str | None = Field(default=None, min_length=1, max_length=200)

class EntityRead(EntityBase):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    created_at: datetime
```

## Migrations

- `alembic revision --autogenerate -m "Description"`
- Make idempotent: check existence before create
- Hand-write migrations for: indexes, data migrations, enum creation
- Use `checkfirst=True` for enum types
- Always include `downgrade()` that reverses changes
- PostGIS: use `from geoalchemy2 import Geometry`

## PostGIS

- SRID 4326 (WGS84) for all geometry columns
- Cast to `::geography` for meter-based distance calculations
- Use `ST_DWithin()` for proximity queries (uses spatial index)

## Middleware DB Session

```python
db = getattr(request.state, "middleware_db", None) or SessionLocal()
```
