"""relabel splynx -> selfcare on uptime source

Revision ID: sc2026062700
Revises: merge2026062601
Create Date: 2026-06-27

Splynx was decommissioned in favour of dotmac_sub (the selfcare client). The
uptime poller already sources presence from dotmac_sub but historical rows were
tagged ``splynx_polling``. Relabel them to ``selfcare_polling`` so the source
label matches reality. Data-only, idempotent, reversible.

Note: subscriber external-id remapping (Splynx integer id -> dotmac_sub UUID) is
intentionally NOT done here — it requires live resolution via the dotmac_sub
``SplynxIdMapping`` bridge and is handled by a separate backfill job.
"""

from alembic import op
from sqlalchemy import text

revision = "sc2026062700"
down_revision = "merge2026062601"
branch_labels = None
depends_on = None

BATCH_SIZE = 10_000


def _relabel_source(table_name: str, old_source: str, new_source: str) -> None:
    bind = op.get_bind()
    statement = text(
        f"""
        WITH batch AS (
            SELECT ctid
            FROM {table_name}
            WHERE source = :old_source
            LIMIT :batch_size
        )
        UPDATE {table_name}
        SET source = :new_source
        FROM batch
        WHERE {table_name}.ctid = batch.ctid
        """
    )
    while True:
        result = bind.execute(
            statement,
            {
                "old_source": old_source,
                "new_source": new_source,
                "batch_size": BATCH_SIZE,
            },
        )
        if result.rowcount == 0:
            break


def upgrade() -> None:
    with op.get_context().autocommit_block():
        _relabel_source("customer_uptime_snapshots", "splynx_polling", "selfcare_polling")
        _relabel_source("customer_uptime_periods", "splynx_polling", "selfcare_polling")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        _relabel_source("customer_uptime_snapshots", "selfcare_polling", "splynx_polling")
        _relabel_source("customer_uptime_periods", "selfcare_polling", "splynx_polling")
