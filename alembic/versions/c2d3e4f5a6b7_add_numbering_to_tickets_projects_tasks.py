"""Add numbering columns for tickets, projects, and project tasks.

Revision ID: c2d3e4f5a6b7
Revises: 7c9d0e1f2a3b, c1b2c3d4e5f6, d8e9f0a1b2c3
Create Date: 2026-02-06
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c2d3e4f5a6b7"
down_revision = ("7c9d0e1f2a3b", "c1b2c3d4e5f6", "d8e9f0a1b2c3")
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE settingdomain ADD VALUE IF NOT EXISTS 'numbering'")
    op.add_column("tickets", sa.Column("number", sa.String(length=40), nullable=True))
    op.add_column("projects", sa.Column("number", sa.String(length=40), nullable=True))
    op.add_column("project_tasks", sa.Column("number", sa.String(length=40), nullable=True))

    op.create_index("uq_tickets_number", "tickets", ["number"], unique=True)
    op.create_index("uq_projects_number", "projects", ["number"], unique=True)
    op.create_index("uq_project_tasks_number", "project_tasks", ["number"], unique=True)


def downgrade():
    op.drop_index("uq_project_tasks_number", table_name="project_tasks")
    op.drop_index("uq_projects_number", table_name="projects")
    op.drop_index("uq_tickets_number", table_name="tickets")

    op.drop_column("project_tasks", "number")
    op.drop_column("projects", "number")
    op.drop_column("tickets", "number")
