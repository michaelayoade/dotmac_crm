"""Backfill Selfcare subscriber and billing-risk report data.

Revision ID: 20260624120000
Revises: mr2026061003
Create Date: 2026-06-24 12:00:00.000000
"""

from __future__ import annotations

import logging

from alembic import op
from sqlalchemy.orm import Session

revision = "20260624120000"
down_revision = "mr2026061003"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def upgrade() -> None:
    bind = op.get_bind()
    session = Session(bind=bind)
    try:
        from app.services.billing_risk_cache import refresh_cache
        from app.services.selfcare import sync_subscribers_from_selfcare_data

        sync_result = sync_subscribers_from_selfcare_data(
            session,
            include_remote_details=False,
            logger=logger,
        )
        if sync_result.get("errors"):
            logger.warning(
                "selfcare_data_migration_partial_errors created=%s updated=%s errors=%s",
                sync_result.get("created", 0),
                sync_result.get("updated", 0),
                len(sync_result.get("errors", [])),
            )

        cache_result = refresh_cache(session, due_soon_days=30, limit=10000)
        logger.info(
            "selfcare_data_migration_complete created=%s updated=%s errors=%s billing_risk_rows=%s",
            sync_result.get("created", 0),
            sync_result.get("updated", 0),
            len(sync_result.get("errors", [])),
            cache_result.get("rows", 0),
        )
    finally:
        session.close()


def downgrade() -> None:
    # Data imported from Selfcare may be linked to CRM activity after migration.
    # Rollback intentionally leaves imported subscriber/report rows in place.
    return None
