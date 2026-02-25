"""
Generate SCHEMA_REF.md for the db-schema Claude Code skill.

Introspects the live PostgreSQL database and produces a complete column-level
reference of all tables, primary keys, foreign keys, indexes, enum types,
and PostGIS geometry columns.  Run after every migration to keep the skill
current.

Usage:
    python scripts/generate_schema_skill.py
    # Or with explicit DSN:
    SCHEMA_DSN=postgresql://postgres:pw@localhost:5432/dotmac_crm python scripts/generate_schema_skill.py
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    import psycopg2 as pg
except ImportError:
    pg = None

# -- Configuration ------------------------------------------------------------

OUTPUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    ".claude",
    "skills",
    "db-schema",
    "SCHEMA_REF.md",
)

# Schemas to document (public is the main one for Omni)
SCHEMAS = ["public"]

# Domain grouping for tables (prefix -> domain label)
DOMAIN_PREFIXES = {
    "crm_": "CRM",
    "ticket": "Tickets",
    "project": "Projects",
    "work_order": "Workforce",
    "dispatch": "Dispatch",
    "inventory": "Inventory",
    "notification": "Notifications",
    "person": "People/Auth",
    "user": "People/Auth",
    "role": "People/Auth",
    "permission": "People/Auth",
    "network_": "Network",
    "fiber_": "Fiber/Network",
    "olt_": "Fiber/Network",
    "pop_": "Network",
    "subscriber": "Subscribers",
    "domain_setting": "Settings",
    "campaign": "CRM/Campaigns",
    "conversation": "CRM/Inbox",
    "message": "CRM/Inbox",
    "lead": "CRM/Sales",
    "quote": "CRM/Sales",
    "vendor": "Vendors",
    "material_request": "Inventory",
    "service_team": "Operations",
    "alembic_version": "System",
    "spatial_ref_sys": "PostGIS",
}


def _classify_table(table_name: str) -> str:
    """Classify a table into a domain based on its name prefix."""
    for prefix, domain in DOMAIN_PREFIXES.items():
        if table_name.startswith(prefix):
            return domain
    return "Other"


# -- DSN helpers --------------------------------------------------------------


def _load_dotenv() -> None:
    """Minimal .env loader."""
    env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(env_file):
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _default_dsn() -> str:
    """Build DSN from .env or defaults."""
    _load_dotenv()
    # Try DOTMAC_OMNI_DB_DSN first, then fall back to DATABASE_URL
    dsn = os.environ.get("DOTMAC_OMNI_DB_DSN") or os.environ.get("DATABASE_URL", "")
    if dsn:
        # Convert SQLAlchemy-style DSN to psycopg2-style
        return dsn.replace("postgresql+psycopg://", "postgresql://").replace(
            "postgresql+psycopg2://", "postgresql://"
        )
    pw = os.environ.get("POSTGRES_PASSWORD", "postgres")
    db = os.environ.get("POSTGRES_DB", "dotmac_crm")
    return f"postgresql://postgres:{pw}@localhost:5432/{db}"


def _redact_dsn(dsn: str) -> str:
    try:
        parts = urlsplit(dsn)
        if not parts.username and not parts.password:
            return dsn
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        if parts.username:
            netloc = f"{parts.username}:***@{host}"
        else:
            netloc = host
        return urlunsplit(
            (parts.scheme, netloc, parts.path, parts.query, parts.fragment)
        )
    except Exception:
        return "<redacted>"


DSN = os.environ.get("SCHEMA_DSN") or _default_dsn()


# -- Database helpers ---------------------------------------------------------


def _connect():
    """Connect to the database."""
    if pg is None:
        print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary", file=sys.stderr)
        sys.exit(1)
    try:
        return pg.connect(DSN)
    except Exception as e:
        print(f"ERROR: Cannot connect to database: {e}", file=sys.stderr)
        print(f"  DSN: {_redact_dsn(DSN)}", file=sys.stderr)
        print("  Set SCHEMA_DSN env var or ensure the DB is running.", file=sys.stderr)
        sys.exit(1)


def _query(cur, sql: str, params: tuple = ()) -> list[tuple]:
    cur.execute(sql, params)
    return cur.fetchall()


def get_schemas(cur) -> list[str]:
    rows = _query(
        cur,
        """
        SELECT schema_name
        FROM information_schema.schemata
        WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
          AND schema_name NOT LIKE 'pg_temp%%'
          AND schema_name NOT LIKE 'pg_toast_temp%%'
        ORDER BY schema_name
    """,
    )
    existing = {row[0] for row in rows}
    ordered = [s for s in SCHEMAS if s in existing]
    for s in sorted(existing):
        if s not in ordered:
            ordered.append(s)
    return ordered


def get_tables(cur, schema: str) -> list[str]:
    rows = _query(
        cur,
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = %s AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """,
        (schema,),
    )
    return [row[0] for row in rows]


def get_columns(cur, schema: str, table: str) -> list[dict[str, str]]:
    rows = _query(
        cur,
        """
        SELECT
            column_name,
            CASE
                WHEN data_type = 'USER-DEFINED' THEN udt_name
                WHEN data_type = 'character varying' THEN 'varchar(' || character_maximum_length || ')'
                WHEN data_type = 'character' THEN 'char(' || character_maximum_length || ')'
                WHEN data_type = 'numeric' THEN 'numeric(' || COALESCE(numeric_precision::text, '?') || ',' || COALESCE(numeric_scale::text, '?') || ')'
                WHEN data_type = 'ARRAY' THEN udt_name
                ELSE data_type
            END as display_type,
            is_nullable,
            column_default
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
    """,
        (schema, table),
    )
    return [
        {"name": row[0], "type": row[1], "nullable": row[2], "default": row[3]}
        for row in rows
    ]


def get_primary_keys(cur, schema: str, table: str) -> list[str]:
    rows = _query(
        cur,
        """
        SELECT a.attname
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid
          AND a.attnum = ANY(i.indkey)
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s AND i.indisprimary
        ORDER BY array_position(i.indkey, a.attnum)
    """,
        (schema, table),
    )
    return [row[0] for row in rows]


def get_foreign_keys(cur, schema: str, table: str) -> list[dict[str, str]]:
    rows = _query(
        cur,
        """
        SELECT
            a_src.attname AS src_column,
            rn.nspname || '.' || rc.relname AS ref_table,
            a_ref.attname AS ref_column
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_class rc ON rc.oid = con.confrelid
        JOIN pg_namespace rn ON rn.oid = rc.relnamespace
        JOIN LATERAL unnest(con.conkey, con.confkey)
             WITH ORDINALITY AS cols(src_attnum, ref_attnum, ord) ON true
        JOIN pg_attribute a_src ON a_src.attrelid = con.conrelid
             AND a_src.attnum = cols.src_attnum
        JOIN pg_attribute a_ref ON a_ref.attrelid = con.confrelid
             AND a_ref.attnum = cols.ref_attnum
        WHERE con.contype = 'f'
          AND n.nspname = %s
          AND c.relname = %s
        ORDER BY con.conname, cols.ord
    """,
        (schema, table),
    )
    return [
        {"column": row[0], "ref_table": row[1], "ref_column": row[2]} for row in rows
    ]


def get_enums(cur) -> dict[str, list[str]]:
    rows = _query(
        cur,
        """
        SELECT t.typname, array_agg(e.enumlabel ORDER BY e.enumsortorder)
        FROM pg_type t
        JOIN pg_enum e ON t.oid = e.enumtypid
        GROUP BY t.typname
        ORDER BY t.typname
    """,
    )
    return {row[0]: row[1] for row in rows}


def get_row_count(cur, schema: str, table: str) -> int:
    rows = _query(
        cur,
        """
        SELECT reltuples::bigint
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s
    """,
        (schema, table),
    )
    return max(0, rows[0][0]) if rows else 0


def get_indexes(cur, schema: str, table: str) -> list[dict[str, Any]]:
    rows = _query(
        cur,
        """
        SELECT
            i.relname AS index_name,
            ix.indisunique AS is_unique,
            array_agg(a.attname ORDER BY array_position(ix.indkey, a.attnum)) AS columns
        FROM pg_index ix
        JOIN pg_class t ON t.oid = ix.indrelid
        JOIN pg_class i ON i.oid = ix.indexrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(ix.indkey)
        WHERE n.nspname = %s
          AND t.relname = %s
          AND NOT ix.indisprimary
        GROUP BY i.relname, ix.indisunique
        ORDER BY i.relname
    """,
        (schema, table),
    )
    return [{"name": r[0], "unique": r[1], "columns": r[2]} for r in rows]


def get_geometry_columns(cur) -> dict[str, dict[str, str]]:
    """Get PostGIS geometry column metadata."""
    try:
        rows = _query(
            cur,
            """
            SELECT f_table_schema, f_table_name, f_geometry_column,
                   type, srid, coord_dimension
            FROM geometry_columns
            ORDER BY f_table_schema, f_table_name
        """,
        )
        result = {}
        for row in rows:
            key = f"{row[0]}.{row[1]}.{row[2]}"
            result[key] = {
                "schema": row[0],
                "table": row[1],
                "column": row[2],
                "type": row[3],
                "srid": row[4],
                "dim": row[5],
            }
        return result
    except Exception:
        return {}


# -- Generator ----------------------------------------------------------------


def generate() -> None:
    """Generate the SCHEMA_REF.md file."""
    conn = _connect()
    cur = conn.cursor()

    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []

    lines.append("# DotMac Omni CRM -- Complete Database Schema Reference")
    lines.append("")
    lines.append(f"*Auto-generated on {now} from live database.*")
    lines.append("*Run `python scripts/generate_schema_skill.py` to regenerate after migrations.*")
    lines.append("")

    # -- Summary --------------------------------------------------------------
    schemas = get_schemas(cur)
    total_tables = 0
    schema_tables: dict[str, list[str]] = {}

    for schema in schemas:
        tables = get_tables(cur, schema)
        schema_tables[schema] = tables
        total_tables += len(tables)

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **{len(schemas)} schemas**, **{total_tables} tables**")
    lines.append("")

    # -- Domain grouping (for public schema) ----------------------------------
    if "public" in schema_tables:
        domain_groups: dict[str, list[str]] = {}
        for table in schema_tables["public"]:
            domain = _classify_table(table)
            domain_groups.setdefault(domain, []).append(table)

        lines.append("## Tables by Domain")
        lines.append("")
        for domain in sorted(domain_groups.keys()):
            tables_list = sorted(domain_groups[domain])
            lines.append(f"### {domain} ({len(tables_list)} tables)")
            lines.append(", ".join(f"`{t}`" for t in tables_list))
            lines.append("")

    # -- PostGIS geometry columns ---------------------------------------------
    geom_cols = get_geometry_columns(cur)
    if geom_cols:
        lines.append("## PostGIS Geometry Columns")
        lines.append("")
        lines.append("| Table | Column | Geometry Type | SRID |")
        lines.append("|-------|--------|--------------|------|")
        for key, info in geom_cols.items():
            lines.append(
                f"| `{info['schema']}.{info['table']}` | `{info['column']}` "
                f"| {info['type']} | {info['srid']} |"
            )
        lines.append("")

    # -- Enum Reference -------------------------------------------------------
    enums = get_enums(cur)
    lines.append(f"## Enum Types ({len(enums)} total)")
    lines.append("")

    for enum_name, values in enums.items():
        val_str = ", ".join(values)
        if len(val_str) > 120:
            val_str = val_str[:117] + "..."
        lines.append(f"- **`{enum_name}`**: {val_str}")

    lines.append("")

    # -- Per-Schema Tables ----------------------------------------------------
    for schema in schemas:
        tables = schema_tables[schema]
        if not tables:
            continue

        lines.append("---")
        lines.append("")
        lines.append(f"## `{schema}` schema ({len(tables)} tables)")
        lines.append("")

        for table in tables:
            pk_cols = get_primary_keys(cur, schema, table)
            columns = get_columns(cur, schema, table)
            fks = get_foreign_keys(cur, schema, table)
            indexes = get_indexes(cur, schema, table)
            row_count = get_row_count(cur, schema, table)

            pk_str = ", ".join(pk_cols) if pk_cols else "none"
            lines.append(f"### `{schema}.{table}`")
            lines.append(f"PK: `{pk_str}` | ~{row_count:,} rows")
            lines.append("")
            lines.append("| Column | Type | Null | Default |")
            lines.append("|--------|------|------|---------|")

            for col in columns:
                nullable = "YES" if col["nullable"] == "YES" else ""
                default = col["default"] or ""
                default = default.replace("|", "\\|")
                if len(default) > 40:
                    default = default[:37] + "..."
                name = col["name"]

                # Mark geometry columns
                geom_key = f"{schema}.{table}.{name}"
                if geom_key in geom_cols:
                    geo = geom_cols[geom_key]
                    name = f"{name} (PostGIS: {geo['type']}, SRID:{geo['srid']})"

                if name.split(" ")[0] in pk_cols:
                    name = f"**{name}** (PK)"

                lines.append(f"| {name} | {col['type']} | {nullable} | {default} |")

            if fks:
                lines.append("")
                lines.append("**Foreign keys:**")
                for fk in fks:
                    lines.append(
                        f"- `{fk['column']}` -> `{fk['ref_table']}.{fk['ref_column']}`"
                    )

            if indexes:
                lines.append("")
                lines.append("**Indexes:**")
                for idx in indexes:
                    cols = ", ".join(idx["columns"])
                    unique = " (unique)" if idx["unique"] else ""
                    lines.append(f"- `{idx['name']}`: ({cols}){unique}")

            lines.append("")

    cur.close()
    conn.close()

    # -- Write output ---------------------------------------------------------
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        f.write("\n".join(lines))

    print(f"Generated: {OUTPUT_PATH}")
    print(f"  {len(schemas)} schemas, {total_tables} tables, {len(enums)} enums")
    print(f"  {len(geom_cols)} PostGIS geometry columns")
    print(f"  {len(lines)} lines written")


if __name__ == "__main__":
    generate()
