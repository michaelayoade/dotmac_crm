"""merge reply_to_message_id with numbering

Revision ID: e467ee5c4c52
Revises: c2d3e4f5a6b7, d0a1b2c3d4e5
Create Date: 2026-02-08 01:03:38.248196

"""

import sqlalchemy as sa
from alembic import op

revision = 'e467ee5c4c52'
down_revision = ('c2d3e4f5a6b7', 'd0a1b2c3d4e5')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
