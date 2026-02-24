# Service Layer Rules

All business logic MUST live in the service layer (`app/services/`).
API routes and web routes are thin wrappers only.

## Manager Pattern

```python
class Entities(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: EntityCreate) -> Entity:
        ...

    @staticmethod
    def get(db: Session, entity_id: str) -> Entity:
        entity = db.get(Entity, coerce_uuid(entity_id))
        if not entity or not entity.is_active:
            raise HTTPException(status_code=404, detail="Not found")
        return entity

    @staticmethod
    def list(db: Session, *, status=None, order_by="created_at", order_dir="desc",
             limit=50, offset=0) -> list[Entity]:
        query = db.query(Entity)
        query = apply_ordering(query, order_by, order_dir, allowed_columns)
        return apply_pagination(query, limit, offset).all()

# Singleton export
entities = Entities()
```

## Key Rules

- **Static methods** on manager classes (no instance state)
- **Inherit `ListResponseMixin`** for paginated list responses
- **Singleton instances** exported at module level with lowercase names
- Use `coerce_uuid()` for ID parameters
- Use `validate_enum()` for enum filters
- Use `apply_ordering()` + `apply_pagination()` for list queries
- Use `get_or_404()` for entity retrieval with eager loading
- Raise `HTTPException(404)` for missing entities
- Soft delete via `is_active = False` (not hard delete)

## Common Utilities (`app/services/common.py`)

| Function | Purpose |
|----------|---------|
| `coerce_uuid(value)` | String → UUID, None-safe |
| `apply_ordering(query, order_by, order_dir, allowed)` | Validated ORDER BY |
| `apply_pagination(query, limit, offset)` | LIMIT/OFFSET |
| `validate_enum(value, enum_cls, label)` | Enum validation |
| `get_or_404(db, model, id, detail)` | Get or raise 404 |
| `round_money(value)` | Banker's rounding to 2 decimals |

## N+1 Prevention

- Never query inside a loop
- Batch load: `db.query(Model).filter(Model.id.in_(ids)).all()`
- Eager load: `joinedload()` or `selectinload()` for relationships
- Build lookup dicts: `{p.id: p for p in persons}`
