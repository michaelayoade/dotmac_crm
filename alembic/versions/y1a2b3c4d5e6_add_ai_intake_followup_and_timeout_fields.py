"""add ai intake followup and timeout fields

Revision ID: y1a2b3c4d5e6
Revises: x9b0c1d2e3f4
Create Date: 2026-03-03 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "y1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "x9b0c1d2e3f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "crm_ai_intake_configs",
        sa.Column(
            "allow_followup_questions",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "crm_ai_intake_configs",
        sa.Column(
            "escalate_after_minutes",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("5"),
        ),
    )


def downgrade() -> None:
    op.drop_column("crm_ai_intake_configs", "escalate_after_minutes")
    op.drop_column("crm_ai_intake_configs", "allow_followup_questions")
