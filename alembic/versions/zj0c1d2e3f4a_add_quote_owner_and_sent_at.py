"""add quote owner and sent timestamp

Revision ID: zj0c1d2e3f4a
Revises: zh8a9b0c1d2e
Create Date: 2026-05-12 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "zj0c1d2e3f4a"
down_revision = "zh8a9b0c1d2e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("crm_quotes", sa.Column("owner_person_id", sa.UUID(), nullable=True))
    op.add_column("crm_quotes", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_crm_quotes_owner_person_id_people",
        "crm_quotes",
        "people",
        ["owner_person_id"],
        ["id"],
    )
    op.create_index("ix_crm_quotes_owner_person_id", "crm_quotes", ["owner_person_id"])

    op.execute(
        """
        UPDATE crm_quotes
        SET owner_person_id = NULLIF(metadata ->> 'owner_person_id', '')::uuid
        WHERE owner_person_id IS NULL
          AND metadata IS NOT NULL
          AND metadata ->> 'owner_person_id' IS NOT NULL
          AND metadata ->> 'owner_person_id' <> ''
        """
    )
    op.execute(
        """
        UPDATE crm_quotes
        SET sent_at = NULLIF(metadata ->> 'sent_at', '')::timestamptz
        WHERE sent_at IS NULL
          AND metadata IS NOT NULL
          AND metadata ->> 'sent_at' IS NOT NULL
          AND metadata ->> 'sent_at' <> ''
        """
    )
    op.execute(
        """
        UPDATE crm_quotes q
        SET owner_person_id = a.person_id
        FROM crm_leads l
        JOIN crm_agents a ON a.id = l.owner_agent_id
        WHERE q.owner_person_id IS NULL
          AND q.lead_id = l.id
          AND a.person_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_crm_quotes_owner_person_id", table_name="crm_quotes")
    op.drop_constraint("fk_crm_quotes_owner_person_id_people", "crm_quotes", type_="foreignkey")
    op.drop_column("crm_quotes", "sent_at")
    op.drop_column("crm_quotes", "owner_person_id")
