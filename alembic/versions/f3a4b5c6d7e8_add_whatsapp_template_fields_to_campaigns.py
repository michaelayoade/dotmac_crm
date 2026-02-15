"""Add WhatsApp template fields to campaigns

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-02-15 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "f3a4b5c6d7e8"
down_revision = "8c1b2a3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("crm_campaigns", sa.Column("whatsapp_template_name", sa.String(200), nullable=True))
    op.add_column("crm_campaigns", sa.Column("whatsapp_template_language", sa.String(10), nullable=True))
    op.add_column("crm_campaigns", sa.Column("whatsapp_template_components", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("crm_campaigns", "whatsapp_template_components")
    op.drop_column("crm_campaigns", "whatsapp_template_language")
    op.drop_column("crm_campaigns", "whatsapp_template_name")
