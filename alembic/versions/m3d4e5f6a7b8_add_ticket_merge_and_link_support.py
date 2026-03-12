"""add ticket merge and link support

Revision ID: m3d4e5f6a7b8
Revises: x9b0c1d2e3f4
Create Date: 2026-03-09 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "m3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "x9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    ticket_status = sa.Enum(
        "new",
        "open",
        "pending",
        "waiting_on_customer",
        "lastmile_rerun",
        "site_under_construction",
        "on_hold",
        "resolved",
        "closed",
        "canceled",
        "merged",
        name="ticketstatus",
    )
    ticket_status.create(bind, checkfirst=True)
    with bind.begin_nested():
        bind.execute(sa.text("ALTER TYPE ticketstatus ADD VALUE IF NOT EXISTS 'merged'"))

    op.create_index("ix_tickets_id_unique", "tickets", ["id"], unique=True)
    op.add_column("tickets", sa.Column("merged_into_ticket_id", sa.UUID(), nullable=True))
    op.create_index("ix_tickets_merged_into_ticket_id", "tickets", ["merged_into_ticket_id"], unique=False)

    op.create_table(
        "ticket_merges",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_ticket_id", sa.UUID(), nullable=False),
        sa.Column("target_ticket_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("merged_by_person_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_ticket_id"),
    )
    op.create_index("ix_ticket_merges_target_ticket_id", "ticket_merges", ["target_ticket_id"], unique=False)

    op.create_table(
        "ticket_links",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("from_ticket_id", sa.UUID(), nullable=False),
        sa.Column("to_ticket_id", sa.UUID(), nullable=False),
        sa.Column("link_type", sa.String(length=40), nullable=False),
        sa.Column("created_by_person_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("from_ticket_id", "link_type", name="uq_ticket_links_from_ticket_link_type"),
        sa.UniqueConstraint("from_ticket_id", "to_ticket_id", "link_type", name="uq_ticket_links_triplet"),
    )
    op.create_index("ix_ticket_links_to_ticket_id", "ticket_links", ["to_ticket_id"], unique=False)
    op.create_index("ix_ticket_links_link_type", "ticket_links", ["link_type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ticket_links_link_type", table_name="ticket_links")
    op.drop_index("ix_ticket_links_to_ticket_id", table_name="ticket_links")
    op.drop_table("ticket_links")

    op.drop_index("ix_ticket_merges_target_ticket_id", table_name="ticket_merges")
    op.drop_table("ticket_merges")

    op.drop_index("ix_tickets_merged_into_ticket_id", table_name="tickets")
    op.drop_column("tickets", "merged_into_ticket_id")
