"""Add indexes for subscriber report queries."""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "za1b2c3d4e5f"
down_revision = "z8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_tickets_active_created_subscriber",
        "tickets",
        ["created_at", "subscriber_id"],
        unique=False,
        if_not_exists=True,
        postgresql_where=sa.text("is_active IS TRUE AND subscriber_id IS NOT NULL"),
    )
    op.create_index(
        "ix_tickets_active_status",
        "tickets",
        ["status"],
        unique=False,
        if_not_exists=True,
        postgresql_where=sa.text("is_active IS TRUE"),
    )
    op.create_index(
        "ix_subscribers_active_activated_at",
        "subscribers",
        ["activated_at"],
        unique=False,
        if_not_exists=True,
        postgresql_where=sa.text("is_active IS TRUE AND activated_at IS NOT NULL"),
    )
    op.create_index(
        "ix_subscribers_active_terminated_at",
        "subscribers",
        ["terminated_at"],
        unique=False,
        if_not_exists=True,
        postgresql_where=sa.text("is_active IS TRUE AND terminated_at IS NOT NULL"),
    )
    op.create_index(
        "ix_subscribers_active_service_region",
        "subscribers",
        ["service_region"],
        unique=False,
        if_not_exists=True,
        postgresql_where=sa.text("is_active IS TRUE AND service_region IS NOT NULL"),
    )
    op.create_index(
        "ix_subscribers_active_service_plan",
        "subscribers",
        ["service_plan"],
        unique=False,
        if_not_exists=True,
        postgresql_where=sa.text("is_active IS TRUE AND service_plan IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_subscribers_active_service_plan", table_name="subscribers", if_exists=True)
    op.drop_index("ix_subscribers_active_service_region", table_name="subscribers", if_exists=True)
    op.drop_index("ix_subscribers_active_terminated_at", table_name="subscribers", if_exists=True)
    op.drop_index("ix_subscribers_active_activated_at", table_name="subscribers", if_exists=True)
    op.drop_index("ix_tickets_active_status", table_name="tickets", if_exists=True)
    op.drop_index("ix_tickets_active_created_subscriber", table_name="tickets", if_exists=True)
