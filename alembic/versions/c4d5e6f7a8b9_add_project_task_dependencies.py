"""add project template/task dependencies

Revision ID: c4d5e6f7a8b9
Revises: 9c7e3c4e8b12
Create Date: 2026-01-30 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM

revision = "c4d5e6f7a8b9"
down_revision = "9c7e3c4e8b12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    enum_type = ENUM(
        "finish_to_start",
        "start_to_start",
        "finish_to_finish",
        "start_to_finish",
        name="taskdependencytype",
    )
    enum_type.create(op.get_bind(), checkfirst=True)

    dependency_enum = ENUM(
        "finish_to_start",
        "start_to_start",
        "finish_to_finish",
        "start_to_finish",
        name="taskdependencytype",
        create_type=False,
    )

    op.create_table(
        "project_template_task_dependency",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column("template_task_id", sa.UUID(), nullable=False),
        sa.Column("depends_on_template_task_id", sa.UUID(), nullable=False),
        sa.Column(
            "dependency_type",
            dependency_enum,
            nullable=False,
            server_default="finish_to_start",
        ),
        sa.Column("lag_days", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["template_task_id"],
            ["project_template_tasks.id"],
        ),
        sa.ForeignKeyConstraint(
            ["depends_on_template_task_id"],
            ["project_template_tasks.id"],
        ),
        sa.UniqueConstraint(
            "template_task_id",
            "depends_on_template_task_id",
            name="uq_project_template_task_dependency",
        ),
        sa.CheckConstraint(
            "template_task_id <> depends_on_template_task_id",
            name="ck_project_template_task_dependency_no_self",
        ),
    )

    op.create_table(
        "project_task_dependencies",
        sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
        sa.Column("task_id", sa.UUID(), nullable=False),
        sa.Column("depends_on_task_id", sa.UUID(), nullable=False),
        sa.Column(
            "dependency_type",
            dependency_enum,
            nullable=False,
            server_default="finish_to_start",
        ),
        sa.Column("lag_days", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["project_tasks.id"],
        ),
        sa.ForeignKeyConstraint(
            ["depends_on_task_id"],
            ["project_tasks.id"],
        ),
        sa.UniqueConstraint(
            "task_id",
            "depends_on_task_id",
            name="uq_project_task_dependencies",
        ),
        sa.CheckConstraint(
            "task_id <> depends_on_task_id",
            name="ck_project_task_dependencies_no_self",
        ),
    )


def downgrade() -> None:
    op.drop_table("project_task_dependencies")
    op.drop_table("project_template_task_dependency")

    dependency_enum = ENUM(
        "finish_to_start",
        "start_to_start",
        "finish_to_finish",
        "start_to_finish",
        name="taskdependencytype",
    )
    dependency_enum.drop(op.get_bind(), checkfirst=True)
