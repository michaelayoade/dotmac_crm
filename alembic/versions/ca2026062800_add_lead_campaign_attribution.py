"""add campaign attribution columns to crm_leads

Revision ID: ca2026062800
Revises: qb3c4d5e6f7a
Create Date: 2026-06-28

Adds campaign_id + campaign_recipient_id to crm_leads so leads can be attributed
back to the marketing campaign that sourced them (campaign ROI).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ca2026062800"
down_revision = "qb3c4d5e6f7a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_leads",
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "crm_leads",
        sa.Column("campaign_recipient_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_crm_leads_campaign_id", "crm_leads", ["campaign_id"])
    op.create_foreign_key(
        "fk_crm_leads_campaign_id", "crm_leads", "crm_campaigns", ["campaign_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_crm_leads_campaign_recipient_id",
        "crm_leads",
        "crm_campaign_recipients",
        ["campaign_recipient_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_crm_leads_campaign_recipient_id", "crm_leads", type_="foreignkey")
    op.drop_constraint("fk_crm_leads_campaign_id", "crm_leads", type_="foreignkey")
    op.drop_index("ix_crm_leads_campaign_id", table_name="crm_leads")
    op.drop_column("crm_leads", "campaign_recipient_id")
    op.drop_column("crm_leads", "campaign_id")
