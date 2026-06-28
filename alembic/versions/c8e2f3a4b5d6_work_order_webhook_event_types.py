"""add work_order_* webhook event types

Lets external apps subscribe to field-visit lifecycle webhooks
(work_order.dispatched / completed / canceled).

Revision ID: c8e2f3a4b5d6
Revises: b7d1e2f3a4c5
Create Date: 2026-06-27 00:00:00.000000
"""

from alembic import op

revision = "c8e2f3a4b5d6"
down_revision = "b7d1e2f3a4c5"
branch_labels = None
depends_on = None

_NEW_VALUES = ("work_order_dispatched", "work_order_completed", "work_order_canceled")


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block.
    with op.get_context().autocommit_block():
        for value in _NEW_VALUES:
            op.execute(f"ALTER TYPE webhookeventtype ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # PostgreSQL cannot DROP an enum value without recreating the type; the added
    # values are inert when unused, so downgrade is intentionally a no-op.
    pass
