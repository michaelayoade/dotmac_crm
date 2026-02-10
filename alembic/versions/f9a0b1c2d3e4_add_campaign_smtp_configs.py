"""Add campaign SMTP configs table and links

Revision ID: f9a0b1c2d3e4
Revises: f7a8b9c0d1e2
Create Date: 2026-02-03 12:50:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "f9a0b1c2d3e4"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
        sa.Column("campaign_smtp_config_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("crm_campaign_smtp_configs.id")),
    )
    op.add_column(
        "notifications",
        sa.Column("smtp_config_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("crm_campaign_smtp_configs.id")),
    )


def downgrade() -> None:
    op.drop_column("notifications", "smtp_config_id")
    op.drop_column("crm_campaigns", "campaign_smtp_config_id")
    op.drop_table("crm_campaign_smtp_configs")
