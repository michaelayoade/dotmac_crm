"""Add campaign senders and notification from fields

Revision ID: f7a8b9c0d1e2
Revises: e1f2a3b4c5d6
Create Date: 2026-02-03 12:30:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "f7a8b9c0d1e2"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crm_campaign_senders",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("from_name", sa.String(160)),
        sa.Column("from_email", sa.String(255), nullable=False),
        sa.Column("reply_to", sa.String(255)),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("from_email", name="uq_crm_campaign_senders_from_email"),
    )

    op.create_table(
        "crm_campaign_smtp_configs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer, server_default="587"),
        sa.Column("username", sa.String(255)),
        sa.Column("password", sa.String(255)),
        sa.Column("use_tls", sa.Boolean, server_default="true"),
        sa.Column("use_ssl", sa.Boolean, server_default="false"),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("name", name="uq_crm_campaign_smtp_configs_name"),
    )

    op.add_column(
        "crm_campaigns",
        sa.Column("campaign_sender_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("crm_campaign_senders.id")),
    )
    op.add_column(
        "crm_campaigns",
        sa.Column("campaign_smtp_config_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("crm_campaign_smtp_configs.id")),
    )

    op.add_column("notifications", sa.Column("from_name", sa.String(160)))
    op.add_column("notifications", sa.Column("from_email", sa.String(255)))
    op.add_column("notifications", sa.Column("reply_to", sa.String(255)))
    op.add_column("notifications", sa.Column("smtp_config_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("crm_campaign_smtp_configs.id")))


def downgrade() -> None:
    op.drop_column("notifications", "smtp_config_id")
    op.drop_column("notifications", "reply_to")
    op.drop_column("notifications", "from_email")
    op.drop_column("notifications", "from_name")
    op.drop_column("crm_campaigns", "campaign_smtp_config_id")
    op.drop_column("crm_campaigns", "campaign_sender_id")
    op.drop_table("crm_campaign_smtp_configs")
    op.drop_table("crm_campaign_senders")
