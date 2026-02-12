"""Drop resellers and reseller_users tables.

Migrates existing reseller data to organizations before dropping.

Revision ID: h2b3c4d5e6f7
Revises: h1a2b3c4d5e6
Create Date: 2026-02-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "h2b3c4d5e6f7"
down_revision = "h1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Migrate existing resellers to organizations (account_type=reseller)
    conn = op.get_bind()
    has_resellers = conn.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'resellers')")
    ).scalar()

    if has_resellers:
        conn.execute(sa.text("""
            INSERT INTO organizations (id, name, email, phone, account_type, account_status, is_active, created_at, updated_at)
            SELECT id, name, contact_email, contact_phone, 'reseller', 'active', is_active, created_at, updated_at
            FROM resellers
            WHERE id NOT IN (SELECT id FROM organizations)
        """))

    # Drop reseller_users first (FK dependency)
    op.drop_table("reseller_users")
    op.drop_table("resellers")


def downgrade() -> None:
    # Recreate resellers table
    op.create_table(
        "resellers",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("code", sa.String(60), unique=True, nullable=True),
        sa.Column("contact_email", sa.String(255), nullable=True),
        sa.Column("contact_phone", sa.String(40), nullable=True),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Recreate reseller_users table
    op.create_table(
        "reseller_users",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("reseller_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("resellers.id"), nullable=False),
        sa.Column("person_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("people.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("reseller_id", "person_id", name="uq_reseller_users_reseller_person"),
    )
