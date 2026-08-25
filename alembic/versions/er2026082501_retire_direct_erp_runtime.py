"""Retire CRM's persisted direct ERP runtime configuration.

Revision ID: er2026082501
Revises: ab2026072401
Create Date: 2026-08-25 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "er2026082501"
down_revision = "ab2026072401"
branch_labels = None
depends_on = None


RETIRED_TASK_NAMES = (
    "app.tasks.integrations.detect_dotmac_erp_identity_drift",
    "app.tasks.integrations.redrive_failed_erp_pushes",
    "app.tasks.integrations.refresh_expense_request_erp_status",
    "app.tasks.integrations.refresh_material_request_erp_status",
    "app.tasks.integrations.refresh_pending_expense_request_erp_statuses",
    "app.tasks.integrations.refresh_pending_material_request_erp_statuses",
    "app.tasks.integrations.sync_dotmac_erp",
    "app.tasks.integrations.sync_dotmac_erp_agents",
    "app.tasks.integrations.sync_dotmac_erp_contacts",
    "app.tasks.integrations.sync_dotmac_erp_entity",
    "app.tasks.integrations.sync_dotmac_erp_inventory",
    "app.tasks.integrations.sync_dotmac_erp_shifts",
    "app.tasks.integrations.sync_dotmac_erp_teams",
    "app.tasks.integrations.sync_dotmac_erp_technicians",
    "app.tasks.integrations.sync_expense_request_to_erp",
    "app.tasks.integrations.sync_material_request_to_erp",
    "app.tasks.integrations.sync_purchase_invoice_to_erp",
    "app.tasks.integrations.sync_purchase_order_to_erp",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("scheduled_tasks"):
        disable_tasks = sa.text("UPDATE scheduled_tasks SET enabled = false WHERE task_name IN :task_names").bindparams(
            sa.bindparam("task_names", expanding=True)
        )
        bind.execute(disable_tasks, {"task_names": RETIRED_TASK_NAMES})

    if inspector.has_table("domain_settings"):
        # Keep the row as retirement evidence, but retain no endpoint, policy,
        # or credential material.  All retired values were scalar settings.
        bind.execute(
            sa.text(
                """
                UPDATE domain_settings
                SET value_type = 'string',
                    value_text = 'retired',
                    value_json = NULL,
                    is_secret = false,
                    is_active = false,
                    updated_at = CURRENT_TIMESTAMP
                WHERE key LIKE 'dotmac_erp_%'
                """
            )
        )


def downgrade() -> None:
    raise RuntimeError(
        "er2026082501 is irreversible: erased secrets and prior schedule enablement cannot be reconstructed"
    )
