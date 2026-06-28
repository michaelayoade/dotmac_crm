"""merge field asset hardening into main

Revision ID: adad6ae61bb0
Revises: ih2026062800, qf7a8b9c0d1e
Create Date: 2026-06-28 20:27:53.060738

"""

from alembic import op
import sqlalchemy as sa


revision = 'adad6ae61bb0'
down_revision = ('ih2026062800', 'qf7a8b9c0d1e')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
