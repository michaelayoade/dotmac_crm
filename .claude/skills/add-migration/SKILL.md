---
name: add-migration
description: Create a safe, idempotent Alembic migration
arguments:
  - name: description
    description: "Short description (e.g. 'add reservations table', 'add index on tickets.region')"
---

# Add Database Migration

Create a safe, idempotent Alembic migration for DotMac Omni CRM.

## Steps

### 1. Generate the migration
```bash
alembic revision --autogenerate -m "$ARGUMENTS"
```

### 2. Review the generated file
Read the newly created migration in `alembic/versions/`.

### 3. Make it idempotent
**CRITICAL**: All migrations MUST be safe to run multiple times:

```python
def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Tables: check before creating
    if not inspector.has_table("my_table"):
        op.create_table(...)

    # Columns: check before adding
    if inspector.has_table("my_table"):
        columns = {col["name"] for col in inspector.get_columns("my_table")}
        if "new_column" not in columns:
            op.add_column(...)

    # Enums: check before creating
    existing_enums = [e["name"] for e in inspector.get_enums()]
    if "my_enum" not in existing_enums:
        my_enum = sa.Enum("val1", "val2", name="my_enum")
        my_enum.create(bind)

    # Indexes: check before creating
    indexes = {idx["name"] for idx in inspector.get_indexes("my_table")}
    if "ix_my_index" not in indexes:
        op.create_index(...)

    # Partial unique indexes (PostgreSQL-specific)
    # Use raw SQL for WHERE clause indexes
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_my_partial
        ON my_table (col_a, col_b) WHERE col_c IS NULL
    """)
```

### 4. PostgreSQL enum gotcha
If adding values to an existing enum:
```python
# ALTER TYPE ... ADD VALUE must run outside a transaction block
# Use `create_type=False` in column definitions
op.execute("ALTER TYPE my_enum ADD VALUE IF NOT EXISTS 'new_value'")
```

Use `create_type=False` in `postgresql.ENUM()` column definitions:
```python
sa.Column("status", sa.Enum(MyEnum, name="myenum", create_type=False))
```

### 5. PostGIS columns
For geometry columns, import GeoAlchemy2:
```python
from geoalchemy2 import Geometry

# In create_table or add_column:
sa.Column("geom", Geometry("POINT", srid=4326))
sa.Column("route_geom", Geometry("LINESTRING", srid=4326))
```

### 6. Always include downgrade
```python
def downgrade() -> None:
    # Reverse ALL changes from upgrade
    op.drop_index("ix_my_index", table_name="my_table")
    op.drop_column("my_table", "new_column")
    # For tables: op.drop_table("my_table")
    # For enums: enum type drops are tricky, often left as-is
```

### 7. Test the migration
```bash
# Apply
alembic upgrade head

# Verify idempotent (run again, should be no-op)
alembic upgrade head

# If inside Docker:
docker compose exec app alembic upgrade head

# Check for errors
docker compose logs app --tail=20
```

### 8. Common patterns

**Data migration (seed data):**
```python
from sqlalchemy import text

def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(text("""
        INSERT INTO domain_settings (id, domain, key, value_text, is_active)
        VALUES (gen_random_uuid(), 'my_domain', 'my_key', 'value', true)
        ON CONFLICT DO NOTHING
    """))
```

**Rename column (safe):**
```python
def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("my_table")}
    if "old_name" in columns and "new_name" not in columns:
        op.alter_column("my_table", "old_name", new_column_name="new_name")
```
