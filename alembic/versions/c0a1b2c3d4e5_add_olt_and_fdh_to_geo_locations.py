"""add olt and fdh to geo_locations

Revision ID: c0a1b2c3d4e5
Revises: b2c3d4e5f6a7
Create Date: 2026-02-04 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c0a1b2c3d4e5"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "geo_locations",
        sa.Column("olt_device_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "geo_locations",
        sa.Column("fdh_cabinet_id", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("geo_locations", "fdh_cabinet_id")
    op.drop_column("geo_locations", "olt_device_id")
