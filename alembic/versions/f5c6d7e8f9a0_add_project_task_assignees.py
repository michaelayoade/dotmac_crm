"""add project task assignees

Revision ID: f5c6d7e8f9a0
Revises: f4b2c3d4e5f6
Create Date: 2026-02-09 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "f5c6d7e8f9a0"
down_revision = "f4b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_task_assignees",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["task_id"], ["project_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("task_id", "person_id"),
    )
    op.create_index("ix_project_task_assignees_person_id", "project_task_assignees", ["person_id"])

    op.execute(
        """
        insert into project_task_assignees (task_id, person_id, created_at)
        select id, assigned_to_person_id, now()
        from project_tasks
        where assigned_to_person_id is not null
        on conflict do nothing
        """
    )


def downgrade() -> None:
    op.drop_index("ix_project_task_assignees_person_id", table_name="project_task_assignees")
    op.drop_table("project_task_assignees")
