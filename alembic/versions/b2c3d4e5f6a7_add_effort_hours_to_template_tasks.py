"""add effort_hours to project_template_tasks

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f7
Create Date: 2026-02-04 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("project_template_tasks", sa.Column("effort_hours", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("project_template_tasks", "effort_hours")
