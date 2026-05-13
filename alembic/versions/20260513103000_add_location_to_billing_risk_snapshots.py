"""Add location to cached subscriber billing risk snapshots."""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260513103000"
down_revision = "20260414100000"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    table_name = "subscriber_billing_risk_snapshots"
    if table_name not in _table_names():
        return

    if "location" not in _column_names(table_name):
        op.add_column(table_name, sa.Column("location", sa.String(length=160), nullable=True))

    if "ix_billing_risk_snapshot_location" not in _index_names(table_name):
        op.create_index("ix_billing_risk_snapshot_location", table_name, ["location"])


def downgrade() -> None:
    table_name = "subscriber_billing_risk_snapshots"
    if table_name not in _table_names():
        return

    if "ix_billing_risk_snapshot_location" in _index_names(table_name):
        op.drop_index("ix_billing_risk_snapshot_location", table_name=table_name)
    if "location" in _column_names(table_name):
        op.drop_column(table_name, "location")
