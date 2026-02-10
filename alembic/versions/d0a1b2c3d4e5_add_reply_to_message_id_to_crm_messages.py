"""add reply_to_message_id to crm_messages

Revision ID: d0a1b2c3d4e5
Revises: c1d2e3f4a5b6
Create Date: 2026-02-07

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d0a1b2c3d4e5"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_messages",
        sa.Column("reply_to_message_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_crm_messages_reply_to",
        "crm_messages",
        "crm_messages",
        ["reply_to_message_id"],
        ["id"],
    )
    op.create_index(
        "ix_crm_messages_reply_to_message_id",
        "crm_messages",
        ["reply_to_message_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_crm_messages_reply_to_message_id", table_name="crm_messages")
    op.drop_constraint("fk_crm_messages_reply_to", "crm_messages", type_="foreignkey")
    op.drop_column("crm_messages", "reply_to_message_id")
