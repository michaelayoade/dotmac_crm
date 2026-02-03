"""merge heads

Revision ID: 317cf417ff24
Revises: 2a4ea51a31ac, e7c1b2a3d4f5
Create Date: 2026-02-01 14:38:40.927776

"""

from alembic import op
import sqlalchemy as sa


revision = '317cf417ff24'
down_revision = ('2a4ea51a31ac', 'e7c1b2a3d4f5')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
