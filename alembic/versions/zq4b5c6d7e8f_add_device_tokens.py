"""add device tokens for mobile push

Revision ID: zq4b5c6d7e8f
Revises: zp3a4b5c6d7e
Create Date: 2026-06-10 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "zq4b5c6d7e8f"
down_revision = "zp3a4b5c6d7e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("device_tokens"):
        return

    device_platform = postgresql.ENUM("android", "ios", name="deviceplatform", create_type=False)
    device_platform.create(bind, checkfirst=True)

    op.create_table(
        "device_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("vendor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("platform", device_platform, nullable=False),
        sa.Column("fcm_token", sa.String(length=512), nullable=False),
        sa.Column("app_version", sa.String(length=40), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["vendor_user_id"], ["vendor_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fcm_token", name="uq_device_tokens_fcm_token"),
    )
    op.create_index("ix_device_tokens_person", "device_tokens", ["person_id"])
    op.create_index("ix_device_tokens_vendor_user", "device_tokens", ["vendor_user_id"])
    op.create_index("ix_device_tokens_fcm_token", "device_tokens", ["fcm_token"])


def downgrade() -> None:
    op.drop_index("ix_device_tokens_fcm_token", table_name="device_tokens")
    op.drop_index("ix_device_tokens_vendor_user", table_name="device_tokens")
    op.drop_index("ix_device_tokens_person", table_name="device_tokens")
    op.drop_table("device_tokens")
    sa.Enum(name="deviceplatform").drop(op.get_bind(), checkfirst=True)
