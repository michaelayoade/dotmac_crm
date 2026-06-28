"""add open/click tracking columns to crm_campaign_recipients

Revision ID: ct2026062800
Revises: ms2026062800
Create Date: 2026-06-28

Adds per-recipient engagement columns (opened_at, clicked_at, open_count,
click_count) so email campaign opens (tracking pixel) and clicks (signed
redirect) can be recorded at recipient granularity.
"""

import sqlalchemy as sa
from alembic import op

revision = "ct2026062800"
down_revision = "ms2026062800"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_campaign_recipients",
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "crm_campaign_recipients",
        sa.Column("clicked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "crm_campaign_recipients",
        sa.Column("open_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "crm_campaign_recipients",
        sa.Column("click_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("crm_campaign_recipients", "click_count")
    op.drop_column("crm_campaign_recipients", "open_count")
    op.drop_column("crm_campaign_recipients", "clicked_at")
    op.drop_column("crm_campaign_recipients", "opened_at")
