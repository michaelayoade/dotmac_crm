"""Add campaign channel and recipient address support

Revision ID: e2f3a4b5c6d7
Revises: d0a1b2c3d4e5
Create Date: 2026-02-14 10:30:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "e2f3a4b5c6d7"
down_revision = "d0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    campaign_channel = sa.Enum("email", "whatsapp", name="campaignchannel")
    campaign_channel.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "crm_campaigns",
        sa.Column("channel", campaign_channel, nullable=True, server_default="email"),
    )
    op.add_column(
        "crm_campaigns",
        sa.Column(
            "connector_config_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("connector_configs.id")
        ),
    )
    op.execute("UPDATE crm_campaigns SET channel = 'email' WHERE channel IS NULL")
    op.alter_column("crm_campaigns", "channel", nullable=False)

    op.add_column("crm_campaign_recipients", sa.Column("address", sa.String(length=255), nullable=True))
    op.execute("UPDATE crm_campaign_recipients SET address = email WHERE address IS NULL")
    op.alter_column("crm_campaign_recipients", "address", nullable=False)
    op.alter_column("crm_campaign_recipients", "email", nullable=True)


def downgrade() -> None:
    op.execute("UPDATE crm_campaign_recipients SET email = address WHERE email IS NULL")
    op.alter_column("crm_campaign_recipients", "email", nullable=False)
    op.drop_column("crm_campaign_recipients", "address")

    op.drop_column("crm_campaigns", "connector_config_id")
    op.drop_column("crm_campaigns", "channel")

    op.execute("DROP TYPE IF EXISTS campaignchannel")
