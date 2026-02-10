"""add ticket assignees

Revision ID: f4b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-02-09 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "f4b2c3d4e5f6"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticket_assignees",
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ticket_id", "person_id"),
    )
    op.create_index("ix_ticket_assignees_person_id", "ticket_assignees", ["person_id"])

    op.execute(
        """
        insert into ticket_assignees (ticket_id, person_id, created_at)
        select id, assigned_to_person_id, now()
        from tickets
        where assigned_to_person_id is not null
        on conflict do nothing
        """
    )


def downgrade() -> None:
    op.drop_index("ix_ticket_assignees_person_id", table_name="ticket_assignees")
    op.drop_table("ticket_assignees")
