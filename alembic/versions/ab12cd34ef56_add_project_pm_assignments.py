"""add project pm assignments

Revision ID: ab12cd34ef56
Revises: fa1b2c3d4e5f
Create Date: 2026-02-03 13:40:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "ab12cd34ef56"
down_revision = "fa1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(sa.Column("project_manager_person_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
        batch_op.add_column(sa.Column("assistant_manager_person_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True))
        batch_op.create_foreign_key(
            "fk_projects_project_manager_person",
            "people",
            ["project_manager_person_id"],
            ["id"],
        )
        batch_op.create_foreign_key(
            "fk_projects_assistant_manager_person",
            "people",
            ["assistant_manager_person_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_constraint("fk_projects_assistant_manager_person", type_="foreignkey")
        batch_op.drop_constraint("fk_projects_project_manager_person", type_="foreignkey")
        batch_op.drop_column("assistant_manager_person_id")
        batch_op.drop_column("project_manager_person_id")
