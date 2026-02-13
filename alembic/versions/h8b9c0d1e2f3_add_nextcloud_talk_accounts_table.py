"""add nextcloud talk accounts table

Revision ID: h8b9c0d1e2f3
Revises: h7a8b9c0d1e2
Create Date: 2026-02-12 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "h8b9c0d1e2f3"
down_revision = "h7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nextcloud_talk_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("people.id"), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("username", sa.String(150), nullable=False),
        sa.Column("app_password_enc", sa.String(2048), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("person_id", name="uq_nextcloud_talk_accounts_person_id"),
    )
    op.create_index(
        "ix_nextcloud_talk_accounts_person_id",
        "nextcloud_talk_accounts",
        ["person_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_nextcloud_talk_accounts_person_id", table_name="nextcloud_talk_accounts")
    op.drop_table("nextcloud_talk_accounts")

