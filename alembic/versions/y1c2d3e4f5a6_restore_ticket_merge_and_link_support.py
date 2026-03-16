"""restore ticket merge and link support

Revision ID: y1c2d3e4f5a6
Revises: v2b3c4d5e6f7
Create Date: 2026-03-12 08:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "y1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "v2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    with bind.begin_nested():
        bind.execute(sa.text("ALTER TYPE ticketstatus ADD VALUE IF NOT EXISTS 'merged'"))

    ticket_columns = {column["name"] for column in inspector.get_columns("tickets")}
    if "merged_into_ticket_id" not in ticket_columns:
        op.add_column("tickets", sa.Column("merged_into_ticket_id", postgresql.UUID(as_uuid=True), nullable=True))

    inspector = inspect(bind)
    if not _has_index(inspector, "tickets", "ix_tickets_merged_into_ticket_id"):
        op.create_index("ix_tickets_merged_into_ticket_id", "tickets", ["merged_into_ticket_id"], unique=False)

    if "ticket_merges" not in inspector.get_table_names():
        op.create_table(
            "ticket_merges",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("source_ticket_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("target_ticket_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("merged_by_person_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["merged_by_person_id"], ["people.id"], ondelete=None),
            sa.ForeignKeyConstraint(["source_ticket_id"], ["tickets.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["target_ticket_id"], ["tickets.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("source_ticket_id"),
        )

    inspector = inspect(bind)
    if not _has_index(inspector, "ticket_merges", "ix_ticket_merges_target_ticket_id"):
        op.create_index("ix_ticket_merges_target_ticket_id", "ticket_merges", ["target_ticket_id"], unique=False)

    if "ticket_links" not in inspector.get_table_names():
        op.create_table(
            "ticket_links",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("from_ticket_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("to_ticket_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("link_type", sa.String(length=40), nullable=False),
            sa.Column("created_by_person_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["created_by_person_id"], ["people.id"], ondelete=None),
            sa.ForeignKeyConstraint(["from_ticket_id"], ["tickets.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["to_ticket_id"], ["tickets.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("from_ticket_id", "link_type", name="uq_ticket_links_from_ticket_link_type"),
            sa.UniqueConstraint("from_ticket_id", "to_ticket_id", "link_type", name="uq_ticket_links_triplet"),
        )

    inspector = inspect(bind)
    if not _has_index(inspector, "ticket_links", "ix_ticket_links_to_ticket_id"):
        op.create_index("ix_ticket_links_to_ticket_id", "ticket_links", ["to_ticket_id"], unique=False)
    if not _has_index(inspector, "ticket_links", "ix_ticket_links_link_type"):
        op.create_index("ix_ticket_links_link_type", "ticket_links", ["link_type"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if "ticket_links" in inspector.get_table_names():
        if _has_index(inspector, "ticket_links", "ix_ticket_links_link_type"):
            op.drop_index("ix_ticket_links_link_type", table_name="ticket_links")
        if _has_index(inspector, "ticket_links", "ix_ticket_links_to_ticket_id"):
            op.drop_index("ix_ticket_links_to_ticket_id", table_name="ticket_links")
        op.drop_table("ticket_links")

    inspector = inspect(bind)
    if "ticket_merges" in inspector.get_table_names():
        if _has_index(inspector, "ticket_merges", "ix_ticket_merges_target_ticket_id"):
            op.drop_index("ix_ticket_merges_target_ticket_id", table_name="ticket_merges")
        op.drop_table("ticket_merges")

    inspector = inspect(bind)
    if "tickets" in inspector.get_table_names():
        if _has_index(inspector, "tickets", "ix_tickets_merged_into_ticket_id"):
            op.drop_index("ix_tickets_merged_into_ticket_id", table_name="tickets")
        ticket_columns = {column["name"] for column in inspector.get_columns("tickets")}
        if "merged_into_ticket_id" in ticket_columns:
            op.drop_column("tickets", "merged_into_ticket_id")
