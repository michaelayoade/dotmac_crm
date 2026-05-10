"""merge conversation summaries and subscriber notifications

Revision ID: 20260508133000
Revises: 20260427093000, zh8a9b0c1d2e
Create Date: 2026-05-08 13:30:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = '20260508133000'
down_revision = ('20260414102000', '20260427093000', 'zh8a9b0c1d2e')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
