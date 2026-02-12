"""Add erpnext_id columns and ExternalEntityType enum values.

Revision ID: h5e6f7a8b9c0
Revises: h4d5e6f7a8b9
Create Date: 2026-02-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "h5e6f7a8b9c0"
down_revision: str | None = "h4d5e6f7a8b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- Add erpnext_id columns --
    op.add_column("tickets", sa.Column("erpnext_id", sa.String(100), nullable=True))
    op.create_index("ix_tickets_erpnext_id", "tickets", ["erpnext_id"], unique=True)

    op.add_column("projects", sa.Column("erpnext_id", sa.String(100), nullable=True))
    op.create_index("ix_projects_erpnext_id", "projects", ["erpnext_id"], unique=True)

    op.add_column("project_tasks", sa.Column("erpnext_id", sa.String(100), nullable=True))
    op.create_index("ix_project_tasks_erpnext_id", "project_tasks", ["erpnext_id"], unique=True)

    op.add_column("people", sa.Column("erpnext_id", sa.String(100), nullable=True))
    op.create_index("ix_people_erpnext_id", "people", ["erpnext_id"], unique=True)

    op.add_column("organizations", sa.Column("erpnext_id", sa.String(100), nullable=True))
    op.create_index("ix_organizations_erpnext_id", "organizations", ["erpnext_id"], unique=True)

    # -- Add new ExternalEntityType enum values --
    bind = op.get_bind()
    bind.execute(sa.text("ALTER TYPE externalentitytype ADD VALUE IF NOT EXISTS 'project_comment'"))
    bind.execute(sa.text("ALTER TYPE externalentitytype ADD VALUE IF NOT EXISTS 'project_task_comment'"))


def downgrade() -> None:
    # -- Remove erpnext_id columns --
    op.drop_index("ix_organizations_erpnext_id", table_name="organizations")
    op.drop_column("organizations", "erpnext_id")

    op.drop_index("ix_people_erpnext_id", table_name="people")
    op.drop_column("people", "erpnext_id")

    op.drop_index("ix_project_tasks_erpnext_id", table_name="project_tasks")
    op.drop_column("project_tasks", "erpnext_id")

    op.drop_index("ix_projects_erpnext_id", table_name="projects")
    op.drop_column("projects", "erpnext_id")

    op.drop_index("ix_tickets_erpnext_id", table_name="tickets")
    op.drop_column("tickets", "erpnext_id")

    # Note: PostgreSQL does not support removing enum values.
    # project_comment and project_task_comment values will remain.
