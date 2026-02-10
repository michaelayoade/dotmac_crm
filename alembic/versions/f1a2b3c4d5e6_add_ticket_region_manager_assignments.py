"""add ticket region and manager assignments

Revision ID: f1a2b3c4d5e6
Revises: d5e6f7a8b9c0
Create Date: 2026-02-09 09:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "f1a2b3c4d5e6"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tickets") as batch_op:
        batch_op.add_column(sa.Column("region", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("ticket_manager_person_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
        batch_op.add_column(sa.Column("assistant_manager_person_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
        batch_op.create_foreign_key(
            "fk_tickets_ticket_manager_person",
            "people",
            ["ticket_manager_person_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            "fk_tickets_assistant_manager_person",
            "people",
            ["assistant_manager_person_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("tickets") as batch_op:
        batch_op.drop_constraint("fk_tickets_assistant_manager_person", type_="foreignkey")
        batch_op.drop_constraint("fk_tickets_ticket_manager_person", type_="foreignkey")
        batch_op.drop_column("assistant_manager_person_id")
        batch_op.drop_column("ticket_manager_person_id")
        batch_op.drop_column("region")
