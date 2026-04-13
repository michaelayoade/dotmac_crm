"""Add ticket list indexes for region and group filters."""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260413123000"
down_revision = "20260412120000"
branch_labels = None
depends_on = None


def _index_names(table_name: str) -> set[str]:
    return {idx["name"] for idx in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    indexes = _index_names("tickets")
    kwargs = {}
    if bind.dialect.name == "postgresql":
        kwargs["postgresql_where"] = sa.text("is_active IS TRUE")
    elif bind.dialect.name == "sqlite":
        kwargs["sqlite_where"] = sa.text("is_active IS TRUE")

    if "ix_tickets_active_region" not in indexes:
        op.create_index("ix_tickets_active_region", "tickets", ["region"], **kwargs)
    if "ix_tickets_active_service_team" not in indexes:
        op.create_index("ix_tickets_active_service_team", "tickets", ["service_team_id"], **kwargs)


def downgrade() -> None:
    indexes = _index_names("tickets")
    if "ix_tickets_active_service_team" in indexes:
        op.drop_index("ix_tickets_active_service_team", table_name="tickets")
    if "ix_tickets_active_region" in indexes:
        op.drop_index("ix_tickets_active_region", table_name="tickets")
