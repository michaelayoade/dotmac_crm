"""add message_outbound to webhookeventtype enum

Revision ID: wh2026062600
Revises: mr2026061003
Create Date: 2026-06-26

Adds the ``message_outbound`` value to the ``webhookeventtype`` enum so that
outbound (agent -> customer) CRM messages can be subscribed to as webhook
events. Idempotent: a no-op where the value already exists.
"""

from alembic import op

revision = "wh2026062600"
down_revision = "mr2026061003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE webhookeventtype ADD VALUE IF NOT EXISTS 'message_outbound'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values without type recreation.
    pass
