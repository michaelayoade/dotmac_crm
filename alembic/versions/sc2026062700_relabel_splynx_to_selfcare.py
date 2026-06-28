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

revision = "sc2026062700"
down_revision = "merge2026062601"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE customer_uptime_snapshots SET source = 'selfcare_polling' "
        "WHERE source = 'splynx_polling'"
    )
    op.execute(
        "UPDATE customer_uptime_periods SET source = 'selfcare_polling' "
        "WHERE source = 'splynx_polling'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE customer_uptime_snapshots SET source = 'splynx_polling' "
        "WHERE source = 'selfcare_polling'"
    )
    op.execute(
        "UPDATE customer_uptime_periods SET source = 'splynx_polling' "
        "WHERE source = 'selfcare_polling'"
    )
