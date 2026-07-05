"""add ticket update/comment webhook event types

Revision ID: zw0b1c2d3e4f
Revises: zy2d3e4f5g6h
Create Date: 2026-07-04 00:00:00.000000
"""

from alembic import op

revision = "zw0b1c2d3e4f"
down_revision = "zy2d3e4f5g6h"
branch_labels = None
depends_on = None

_NEW_VALUES = ("ticket_updated", "ticket_comment_created")


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block.
    with op.get_context().autocommit_block():
        for value in _NEW_VALUES:
            op.execute(f"ALTER TYPE webhookeventtype ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # PostgreSQL cannot DROP enum values without recreating the type.
    pass
