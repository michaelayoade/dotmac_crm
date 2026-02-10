"""Add project_id and work_order_id to event_store

Revision ID: 7a471f513013
Revises: 7c9d0e1f2a3b
Create Date: 2026-02-01

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '7a471f513013'
down_revision: str | None = '7c9d0e1f2a3b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add project_id column with index
    op.add_column(
        'event_store',
        sa.Column('project_id', postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_index(
        'ix_event_store_project_id',
        'event_store',
        ['project_id'],
        unique=False
    )

    # Add work_order_id column with index
    op.add_column(
        'event_store',
        sa.Column('work_order_id', postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_index(
        'ix_event_store_work_order_id',
        'event_store',
        ['work_order_id'],
        unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_event_store_work_order_id', table_name='event_store')
    op.drop_column('event_store', 'work_order_id')
    op.drop_index('ix_event_store_project_id', table_name='event_store')
    op.drop_column('event_store', 'project_id')
