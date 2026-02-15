"""add nextcloud talk notification room cache

Revision ID: m1a2b3c4d5f0
Revises: l1a2b3c4d5e9
Create Date: 2026-02-14 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "m1a2b3c4d5f0"
down_revision = "l1a2b3c4d5e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nextcloud_talk_notification_rooms",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("people.id"), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("notifier_username", sa.String(150), nullable=False),
        sa.Column("invite_target", sa.String(255), nullable=False),
        sa.Column("room_token", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "person_id",
            "base_url",
            "notifier_username",
            name="uq_nextcloud_talk_notification_rooms_person_instance",
        ),
    )
    op.create_index(
        "ix_nextcloud_talk_notification_rooms_person_id",
        "nextcloud_talk_notification_rooms",
        ["person_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_nextcloud_talk_notification_rooms_person_id", table_name="nextcloud_talk_notification_rooms")
    op.drop_table("nextcloud_talk_notification_rooms")
