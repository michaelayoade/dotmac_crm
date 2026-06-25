"""Add client_ref to work_logs for offline worklog idempotency."""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "wl2026062500"
down_revision = "mr2026061003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_columns = {column["name"] for column in inspector.get_columns("work_logs")}
    if "client_ref" not in existing_columns:
        op.add_column("work_logs", sa.Column("client_ref", sa.UUID(), nullable=True))

    # Unique index dedupes retried offline uploads. Nullable: pre-existing rows
    # keep NULL, and Postgres allows many NULLs under a unique index.
    existing_indexes = {index["name"] for index in inspector.get_indexes("work_logs")}
    if "ix_work_logs_client_ref" not in existing_indexes:
        op.create_index(
            "ix_work_logs_client_ref",
            "work_logs",
            ["client_ref"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_indexes = {index["name"] for index in inspector.get_indexes("work_logs")}
    if "ix_work_logs_client_ref" in existing_indexes:
        op.drop_index("ix_work_logs_client_ref", table_name="work_logs")

    existing_columns = {column["name"] for column in inspector.get_columns("work_logs")}
    if "client_ref" in existing_columns:
        op.drop_column("work_logs", "client_ref")
