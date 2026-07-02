"""ticket resolution confirmation: pending_confirmation status + access tokens

Revision ID: fe2026070201
Revises: fe2026070200
Create Date: 2026-07-02

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "fe2026070201"
down_revision = "fe2026070200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE ticketstatus ADD VALUE IF NOT EXISTS 'pending_confirmation'")

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "ticket_access_tokens" not in inspector.get_table_names():
        op.create_table(
            "ticket_access_tokens",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("ticket_id", UUID(as_uuid=True), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("token", sa.String(64), nullable=False),
            sa.Column("purpose", sa.String(40), nullable=False, server_default="resolution_confirm"),
            sa.Column("expires_at", sa.DateTime(timezone=True)),
            sa.Column("accessed_at", sa.DateTime(timezone=True)),
            sa.Column("responded_at", sa.DateTime(timezone=True)),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_ticket_access_tokens_token", "ticket_access_tokens", ["token"], unique=True)
        op.create_index("ix_ticket_access_tokens_ticket_id", "ticket_access_tokens", ["ticket_id"])


def downgrade() -> None:
    op.drop_index("ix_ticket_access_tokens_ticket_id", table_name="ticket_access_tokens")
    op.drop_index("ix_ticket_access_tokens_token", table_name="ticket_access_tokens")
    op.drop_table("ticket_access_tokens")
    # PostgreSQL cannot drop an enum value without recreating the type; leave
    # 'pending_confirmation' in place (harmless).
