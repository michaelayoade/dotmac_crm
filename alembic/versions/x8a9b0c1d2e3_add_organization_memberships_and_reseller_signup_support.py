"""Add organization memberships for multi-org access (reseller child accounts).

Revision ID: x8a9b0c1d2e3
Revises: w7a8b9c0d1e2
Create Date: 2026-02-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "x8a9b0c1d2e3"
down_revision = "w7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the enum type idempotently, but prevent CREATE TYPE from being
    # re-emitted during CREATE TABLE (alembic create_table uses checkfirst=False).
    role_enum_create = postgresql.ENUM(
        "owner",
        "admin",
        "member",
        name="organizationmembershiprole",
        create_type=True,
    )
    role_enum_create.create(op.get_bind(), checkfirst=True)

    role_enum = postgresql.ENUM(
        "owner",
        "admin",
        "member",
        name="organizationmembershiprole",
        create_type=False,
    )

    op.create_table(
        "organization_memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", role_enum, nullable=False, server_default="member"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "person_id", name="uq_organization_memberships_org_person"),
    )
    op.create_index(
        "ix_organization_memberships_org",
        "organization_memberships",
        ["organization_id"],
    )
    op.create_index(
        "ix_organization_memberships_person",
        "organization_memberships",
        ["person_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_organization_memberships_person", table_name="organization_memberships")
    op.drop_index("ix_organization_memberships_org", table_name="organization_memberships")
    op.drop_table("organization_memberships")

    role_enum_drop = postgresql.ENUM(name="organizationmembershiprole")
    role_enum_drop.drop(op.get_bind(), checkfirst=True)
