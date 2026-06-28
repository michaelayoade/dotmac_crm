"""add fiber test results

Revision ID: qe6f7a8b9c0d
Revises: qd5e6f7a8b9c
Create Date: 2026-06-28 00:20:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "qe6f7a8b9c0d"
down_revision = "qd5e6f7a8b9c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("fiber_test_results"):
        return

    op.create_table(
        "fiber_test_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("asset_type", sa.String(length=80), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("test_type", sa.String(length=40), nullable=False),
        sa.Column("wavelength_nm", sa.Integer(), nullable=True),
        sa.Column("value_db", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=16), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("instrument", sa.String(length=120), nullable=True),
        sa.Column("attachment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("measured_by_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("client_ref", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"]),
        sa.ForeignKeyConstraint(["attachment_id"], ["field_attachments.id"]),
        sa.ForeignKeyConstraint(["measured_by_person_id"], ["people.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fiber_test_results_asset", "fiber_test_results", ["asset_type", "asset_id"])
    op.create_index("ix_fiber_test_results_client_ref", "fiber_test_results", ["client_ref"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_fiber_test_results_client_ref", table_name="fiber_test_results")
    op.drop_index("ix_fiber_test_results_asset", table_name="fiber_test_results")
    op.drop_table("fiber_test_results")
