"""add meta raw events

Revision ID: 20260508133001
Revises: 20260508133000, zh8a9b0c1d2e
Create Date: 2026-05-08 13:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260508133001"
down_revision: str | Sequence[str] | None = ("20260508133000", "zh8a9b0c1d2e")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "meta_raw_events" not in inspector.get_table_names():
        op.create_table(
            "meta_raw_events",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("platform", sa.String(length=40), nullable=False),
            sa.Column("sender_id", sa.String(length=255), nullable=True),
            sa.Column("page_id", sa.String(length=255), nullable=True),
            sa.Column("event_type", sa.String(length=40), nullable=False),
            sa.Column("external_message_id", sa.String(length=255), nullable=True),
            sa.Column("trace_id", sa.String(length=64), nullable=True),
            sa.Column("dedupe_key", sa.String(length=64), nullable=False),
            sa.Column("raw_payload", sa.JSON(), nullable=False),
            sa.Column("attribution", sa.JSON(), nullable=True),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("dedupe_key", name="uq_meta_raw_events_dedupe_key"),
        )

    indexes = {index["name"] for index in inspector.get_indexes("meta_raw_events")}
    if "ix_meta_raw_events_platform_received" not in indexes:
        op.create_index(
            "ix_meta_raw_events_platform_received",
            "meta_raw_events",
            ["platform", "received_at"],
            unique=False,
        )
    if "ix_meta_raw_events_sender_page" not in indexes:
        op.create_index(
            "ix_meta_raw_events_sender_page",
            "meta_raw_events",
            ["platform", "sender_id", "page_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index("ix_meta_raw_events_sender_page", table_name="meta_raw_events")
    op.drop_index("ix_meta_raw_events_platform_received", table_name="meta_raw_events")
    op.drop_table("meta_raw_events")
