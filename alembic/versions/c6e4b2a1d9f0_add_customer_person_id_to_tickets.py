"""Add customer_person_id to tickets

Revision ID: c6e4b2a1d9f0
Revises: 0345c81d2b40, f3a1b9c2d4e5
Create Date: 2026-02-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c6e4b2a1d9f0"
down_revision: Union[str, Sequence[str], None] = ("0345c81d2b40", "f3a1b9c2d4e5")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("customer_person_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_tickets_customer_person_id",
        "tickets",
        "people",
        ["customer_person_id"],
        ["id"],
    )
    op.create_index(
        "ix_tickets_customer_person_id",
        "tickets",
        ["customer_person_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_tickets_customer_person_id", table_name="tickets")
    op.drop_constraint(
        "fk_tickets_customer_person_id",
        "tickets",
        type_="foreignkey",
    )
    op.drop_column("tickets", "customer_person_id")
