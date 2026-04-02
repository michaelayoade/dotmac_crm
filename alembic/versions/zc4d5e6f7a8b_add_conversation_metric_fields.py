"""add conversation metric fields"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "zc4d5e6f7a8b"
down_revision = "zb2c3d4e5f6a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_conversations",
        sa.Column("first_response_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "crm_conversations",
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "crm_conversations",
        sa.Column("response_time_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "crm_conversations",
        sa.Column("resolution_time_seconds", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_crm_conversations_first_response_at",
        "crm_conversations",
        ["first_response_at"],
    )
    op.create_index(
        "ix_crm_conversations_resolved_at",
        "crm_conversations",
        ["resolved_at"],
    )
    op.create_index(
        "ix_crm_conversations_status_first_response",
        "crm_conversations",
        ["status", "first_response_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_crm_conversations_status_first_response",
        table_name="crm_conversations",
    )
    op.drop_index(
        "ix_crm_conversations_resolved_at",
        table_name="crm_conversations",
    )
    op.drop_index(
        "ix_crm_conversations_first_response_at",
        table_name="crm_conversations",
    )
    op.drop_column("crm_conversations", "resolution_time_seconds")
    op.drop_column("crm_conversations", "response_time_seconds")
    op.drop_column("crm_conversations", "resolved_at")
    op.drop_column("crm_conversations", "first_response_at")
