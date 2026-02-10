"""add service_team_id FK to crm_teams, tickets, projects

Revision ID: g2b3c4d5e6f7
Revises: g1a2b3c4d5e6
Create Date: 2026-02-10 00:01:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "g2b3c4d5e6f7"
down_revision = "g1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("crm_teams", sa.Column("service_team_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_crm_teams_service_team_id", "crm_teams", "service_teams", ["service_team_id"], ["id"])
    op.create_index("ix_crm_teams_service_team_id", "crm_teams", ["service_team_id"])

    op.add_column("tickets", sa.Column("service_team_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_tickets_service_team_id", "tickets", "service_teams", ["service_team_id"], ["id"])
    op.create_index("ix_tickets_service_team_id", "tickets", ["service_team_id"])

    op.add_column("projects", sa.Column("service_team_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_projects_service_team_id", "projects", "service_teams", ["service_team_id"], ["id"])
    op.create_index("ix_projects_service_team_id", "projects", ["service_team_id"])


def downgrade() -> None:
    op.drop_index("ix_projects_service_team_id", table_name="projects")
    op.drop_constraint("fk_projects_service_team_id", "projects", type_="foreignkey")
    op.drop_column("projects", "service_team_id")

    op.drop_index("ix_tickets_service_team_id", table_name="tickets")
    op.drop_constraint("fk_tickets_service_team_id", "tickets", type_="foreignkey")
    op.drop_column("tickets", "service_team_id")

    op.drop_index("ix_crm_teams_service_team_id", table_name="crm_teams")
    op.drop_constraint("fk_crm_teams_service_team_id", "crm_teams", type_="foreignkey")
    op.drop_column("crm_teams", "service_team_id")
