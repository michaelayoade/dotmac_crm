"""add vendor quote submitted automation rule

Revision ID: i1a2b3c4d5e6
Revises: ha0b1c2d3e6
Create Date: 2026-02-13 14:10:00.000000
"""

import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "i1a2b3c4d5e6"
down_revision = "ha0b1c2d3e6"
branch_labels = None
depends_on = None

_RULE_NAME = "Vendor Quote Submitted -> Create/Update Work Order"
_EVENT_TYPE = "vendor_quote.submitted"


def upgrade() -> None:
    bind = op.get_bind()
    existing = bind.execute(
        sa.text(
            """
            SELECT id
            FROM automation_rules
            WHERE name = :name
              AND event_type = :event_type
            LIMIT 1
            """
        ),
        {"name": _RULE_NAME, "event_type": _EVENT_TYPE},
    ).scalar()
    if existing:
        return

    bind.execute(
        sa.text(
            """
            INSERT INTO automation_rules (
                id,
                name,
                description,
                event_type,
                conditions,
                actions,
                status,
                priority,
                stop_after_match,
                cooldown_seconds,
                execution_count,
                is_active,
                created_at,
                updated_at
            )
            VALUES (
                :id,
                :name,
                :description,
                :event_type,
                CAST(:conditions AS jsonb),
                CAST(:actions AS jsonb),
                'active'::automationrulestatus,
                100,
                true,
                0,
                0,
                true,
                NOW(),
                NOW()
            )
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "name": _RULE_NAME,
            "description": (
                "Creates a work order when a vendor submits a quote and updates the existing "
                "automation-created work order on subsequent submissions."
            ),
            "event_type": _EVENT_TYPE,
            "conditions": json.dumps([]),
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


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            DELETE FROM automation_rules
            WHERE name = :name
              AND event_type = :event_type
            """
        ),
        {"name": _RULE_NAME, "event_type": _EVENT_TYPE},
    )
