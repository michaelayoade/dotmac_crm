"""Add ERP sync fields to dispatch models

Revision ID: b5c6d7e8f9a0
Revises: a3b4c5d6e7f8
Create Date: 2026-02-02

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b5c6d7e8f9a0"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add erp_employee_id to technician_profiles
    op.add_column(
        "technician_profiles",
        sa.Column("erp_employee_id", sa.String(100), nullable=True),
    )
    op.create_index(
        "ix_technician_profiles_erp_employee_id",
        "technician_profiles",
        ["erp_employee_id"],
        unique=True,
    )

    # Add shift_type, erp_id, updated_at to shifts
    op.add_column(
        "shifts",
        sa.Column("shift_type", sa.String(60), nullable=True),
    )
    op.add_column(
        "shifts",
        sa.Column("erp_id", sa.String(100), nullable=True),
    )
    op.add_column(
        "shifts",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_shifts_erp_id",
        "shifts",
        ["erp_id"],
        unique=True,
    )

    # Add block_type, erp_id, updated_at to availability_blocks
    op.add_column(
        "availability_blocks",
        sa.Column("block_type", sa.String(60), nullable=True),
    )
    op.add_column(
        "availability_blocks",
        sa.Column("erp_id", sa.String(100), nullable=True),
    )
    op.add_column(
        "availability_blocks",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_availability_blocks_erp_id",
        "availability_blocks",
        ["erp_id"],
        unique=True,
    )


def downgrade() -> None:
    # Remove from availability_blocks
    op.drop_index("ix_availability_blocks_erp_id", table_name="availability_blocks")
    op.drop_column("availability_blocks", "updated_at")
    op.drop_column("availability_blocks", "erp_id")
    op.drop_column("availability_blocks", "block_type")

    # Remove from shifts
    op.drop_index("ix_shifts_erp_id", table_name="shifts")
    op.drop_column("shifts", "updated_at")
    op.drop_column("shifts", "erp_id")
    op.drop_column("shifts", "shift_type")

    # Remove from technician_profiles
    op.drop_index("ix_technician_profiles_erp_employee_id", table_name="technician_profiles")
    op.drop_column("technician_profiles", "erp_employee_id")
