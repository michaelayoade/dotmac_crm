"""Add lead_id to tickets and projects

Revision ID: f3a1b9c2d4e5
Revises: 9f8e7d6c5b4a, 7a471f513013
Create Date: 2026-02-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f3a1b9c2d4e5"
down_revision: Union[str, Sequence[str], None] = ("9f8e7d6c5b4a", "7a471f513013")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tickets",
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_tickets_lead_id",
        "tickets",
        "crm_leads",
        ["lead_id"],
        ["id"],
    )
    op.create_index("ix_tickets_lead_id", "tickets", ["lead_id"], unique=False)

    op.add_column(
        "projects",
        sa.Column("lead_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_projects_lead_id",
        "projects",
        "crm_leads",
        ["lead_id"],
        ["id"],
    )
    op.create_index("ix_projects_lead_id", "projects", ["lead_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_projects_lead_id", table_name="projects")
    op.drop_constraint("fk_projects_lead_id", "projects", type_="foreignkey")
    op.drop_column("projects", "lead_id")

    op.drop_index("ix_tickets_lead_id", table_name="tickets")
    op.drop_constraint("fk_tickets_lead_id", "tickets", type_="foreignkey")
    op.drop_column("tickets", "lead_id")
