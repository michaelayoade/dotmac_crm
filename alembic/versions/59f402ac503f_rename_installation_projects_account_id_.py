"""rename installation_projects account_id to subscriber_id

Revision ID: 59f402ac503f
Revises: af8fbbefa221
Create Date: 2026-01-27 12:32:20.180136

"""

from alembic import op
import sqlalchemy as sa


revision = '59f402ac503f'
down_revision = 'af8fbbefa221'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("installation_projects") as batch_op:
        batch_op.alter_column("account_id", new_column_name="subscriber_id")
        batch_op.create_foreign_key(
            "fk_installation_projects_subscriber_id_subscribers",
            "subscribers",
            ["subscriber_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_installation_projects_subscriber_id",
            ["subscriber_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("installation_projects") as batch_op:
        batch_op.drop_index("ix_installation_projects_subscriber_id")
        batch_op.drop_constraint(
            "fk_installation_projects_subscriber_id_subscribers", type_="foreignkey"
        )
        batch_op.alter_column("subscriber_id", new_column_name="account_id")
