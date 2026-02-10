"""Add B2B account model fields

Revision ID: c7d8e9f0a1b2
Revises: b5c6d7e8f9a0
Create Date: 2026-02-02

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSON, UUID

# revision identifiers, used by Alembic.
revision = "c7d8e9f0a1b2"
down_revision = "b5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enums
    account_type_enum = sa.Enum(
        "prospect", "customer", "partner", "reseller", "vendor", "competitor", "other",
        name="accounttype"
    )
    account_status_enum = sa.Enum(
        "active", "inactive", "churned", "suspended", "archived",
        name="accountstatus"
    )
    account_type_enum.create(op.get_bind(), checkfirst=True)
    account_status_enum.create(op.get_bind(), checkfirst=True)

    # Add new columns to organizations
    op.add_column("organizations", sa.Column("phone", sa.String(40), nullable=True))
    op.add_column("organizations", sa.Column("email", sa.String(255), nullable=True))
    op.add_column(
        "organizations",
        sa.Column("account_type", account_type_enum, server_default="prospect", nullable=False),
    )
    op.add_column(
        "organizations",
        sa.Column("account_status", account_status_enum, server_default="active", nullable=False),
    )
    op.add_column(
        "organizations",
        sa.Column("parent_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("primary_contact_id", UUID(as_uuid=True), sa.ForeignKey("people.id"), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("owner_id", UUID(as_uuid=True), sa.ForeignKey("people.id"), nullable=True),
    )
    op.add_column("organizations", sa.Column("industry", sa.String(100), nullable=True))
    op.add_column("organizations", sa.Column("employee_count", sa.String(40), nullable=True))
    op.add_column("organizations", sa.Column("annual_revenue", sa.String(60), nullable=True))
    op.add_column("organizations", sa.Column("source", sa.String(100), nullable=True))
    op.add_column("organizations", sa.Column("erp_id", sa.String(100), nullable=True))
    op.add_column("organizations", sa.Column("tags", JSON, nullable=True))
    op.add_column("organizations", sa.Column("metadata", JSON, nullable=True))
    op.add_column("organizations", sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False))

    # Create indexes for organizations
    op.create_index("ix_organizations_parent", "organizations", ["parent_id"])
    op.create_index("ix_organizations_account_type", "organizations", ["account_type"])
    op.create_index("ix_organizations_status", "organizations", ["account_status"])
    op.create_index("ix_organizations_owner", "organizations", ["owner_id"])
    op.create_index("ix_organizations_erp", "organizations", ["erp_id"], unique=True)

    # Add job_title to people
    op.add_column("people", sa.Column("job_title", sa.String(120), nullable=True))


def downgrade() -> None:
    # Remove job_title from people
    op.drop_column("people", "job_title")

    # Drop indexes from organizations
    op.drop_index("ix_organizations_erp", table_name="organizations")
    op.drop_index("ix_organizations_owner", table_name="organizations")
    op.drop_index("ix_organizations_status", table_name="organizations")
    op.drop_index("ix_organizations_account_type", table_name="organizations")
    op.drop_index("ix_organizations_parent", table_name="organizations")

    # Remove columns from organizations
    op.drop_column("organizations", "is_active")
    op.drop_column("organizations", "metadata")
    op.drop_column("organizations", "tags")
    op.drop_column("organizations", "erp_id")
    op.drop_column("organizations", "source")
    op.drop_column("organizations", "annual_revenue")
    op.drop_column("organizations", "employee_count")
    op.drop_column("organizations", "industry")
    op.drop_column("organizations", "owner_id")
    op.drop_column("organizations", "primary_contact_id")
    op.drop_column("organizations", "parent_id")
    op.drop_column("organizations", "account_status")
    op.drop_column("organizations", "account_type")
    op.drop_column("organizations", "email")
    op.drop_column("organizations", "phone")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS accountstatus")
    op.execute("DROP TYPE IF EXISTS accounttype")
