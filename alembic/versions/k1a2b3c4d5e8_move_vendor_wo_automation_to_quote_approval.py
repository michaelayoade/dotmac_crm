"""move vendor wo automation to quote approval

Revision ID: k1a2b3c4d5e8
Revises: j1a2b3c4d5e7
Create Date: 2026-02-13 16:10:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "k1a2b3c4d5e8"
down_revision = "j1a2b3c4d5e7"
branch_labels = None
depends_on = None

_OLD_EVENT_TYPE = "vendor_quote.submitted"
_NEW_EVENT_TYPE = "vendor_quote.approved"
_RULE_NAME = "Vendor Quote Submitted -> Create/Update Work Order"
_NEW_RULE_NAME = "Vendor Quote Approved -> Create/Update Work Order"


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE automation_rules
            SET event_type = :new_event_type,
                name = :new_rule_name,
                updated_at = NOW()
            WHERE (name = :rule_name OR name = :new_rule_name)
              AND event_type IN (:old_event_type, :new_event_type)
            """
        ),
        {
            "new_event_type": _NEW_EVENT_TYPE,
            "new_rule_name": _NEW_RULE_NAME,
            "rule_name": _RULE_NAME,
            "old_event_type": _OLD_EVENT_TYPE,
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE automation_rules
            SET event_type = :old_event_type,
                name = :rule_name,
                updated_at = NOW()
            WHERE (name = :rule_name OR name = :new_rule_name)
              AND event_type IN (:old_event_type, :new_event_type)
            """
        ),
        {
            "new_event_type": _NEW_EVENT_TYPE,
            "new_rule_name": _NEW_RULE_NAME,
            "rule_name": _RULE_NAME,
            "old_event_type": _OLD_EVENT_TYPE,
        },
    )
