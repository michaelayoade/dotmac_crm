"""make vendor quote work order title dynamic

Revision ID: j1a2b3c4d5e7
Revises: i1a2b3c4d5e6
Create Date: 2026-02-13 15:05:00.000000
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "j1a2b3c4d5e7"
down_revision = "i1a2b3c4d5e6"
branch_labels = None
depends_on = None

_RULE_NAME = "Vendor Quote Submitted -> Create/Update Work Order"
_EVENT_TYPE = "vendor_quote.submitted"


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE automation_rules
            SET actions = CAST(:actions AS jsonb),
                updated_at = NOW()
            WHERE name = :name
              AND event_type = :event_type
            """
        ),
        {
            "name": _RULE_NAME,
            "event_type": _EVENT_TYPE,
            "actions": json.dumps(
                [
                    {
                        "action_type": "create_work_order",
                        "params": {
                            "title_template": "Vendor Quote WO - {project_code} - {vendor_name}",
                            "upsert_existing": True,
                            "match_title_exact": True,
                            "source_name": "vendor_quote_work_order_automation",
                        },
                    }
                ]
            ),
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE automation_rules
            SET actions = CAST(:actions AS jsonb),
                updated_at = NOW()
            WHERE name = :name
              AND event_type = :event_type
            """
        ),
        {
            "name": _RULE_NAME,
            "event_type": _EVENT_TYPE,
            "actions": json.dumps(
                [
                    {
                        "action_type": "create_work_order",
                        "params": {
                            "title": "Vendor Quote Work Order",
                            "upsert_existing": True,
                            "match_title_exact": True,
                            "source_name": "vendor_quote_work_order_automation",
                        },
                    }
                ]
            ),
        },
    )
