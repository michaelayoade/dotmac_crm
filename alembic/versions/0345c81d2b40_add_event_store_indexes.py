"""Add missing indexes to event_store

Revision ID: 0345c81d2b40
Revises: 7a471f513013
Create Date: 2026-02-01

"""
from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0345c81d2b40'
down_revision: str | None = '7a471f513013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add index on ticket_id for filtering events by ticket
    op.create_index(
        'ix_event_store_ticket_id',
        'event_store',
        ['ticket_id'],
        unique=False
    )

    # Add composite index on status + created_at for stale event queries
    op.create_index(
        'ix_event_store_status_created_at',
        'event_store',
        ['status', 'created_at'],
        unique=False
    )

    # Add composite index on status + retry_count for failed event retry logic
    op.create_index(
        'ix_event_store_status_retry_count',
        'event_store',
        ['status', 'retry_count'],
        unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_event_store_status_retry_count', table_name='event_store')
    op.drop_index('ix_event_store_status_created_at', table_name='event_store')
    op.drop_index('ix_event_store_ticket_id', table_name='event_store')
