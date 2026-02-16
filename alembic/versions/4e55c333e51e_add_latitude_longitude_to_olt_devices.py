"""Add latitude longitude to olt_devices

Revision ID: 4e55c333e51e
Revises: r1a2b3c4d5f4
Create Date: 2026-02-16 09:58:50.347422

"""

from alembic import op
import sqlalchemy as sa


revision = '4e55c333e51e'
down_revision = 'r1a2b3c4d5f4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('olt_devices', sa.Column('latitude', sa.Float(), nullable=True))
    op.add_column('olt_devices', sa.Column('longitude', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('olt_devices', 'longitude')
    op.drop_column('olt_devices', 'latitude')
