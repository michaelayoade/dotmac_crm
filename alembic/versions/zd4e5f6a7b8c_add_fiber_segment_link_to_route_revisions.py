"""Add fiber segment link to proposed route revisions."""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "zd4e5f6a7b8c"
down_revision = "zc3d4e5f6a7b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "proposed_route_revisions",
        sa.Column("fiber_segment_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_proposed_route_revisions_fiber_segment_id",
        "proposed_route_revisions",
        "fiber_segments",
        ["fiber_segment_id"],
        ["id"],
    )
    op.create_index(
        "ix_proposed_route_revisions_fiber_segment_id",
        "proposed_route_revisions",
        ["fiber_segment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_proposed_route_revisions_fiber_segment_id", table_name="proposed_route_revisions")
    op.drop_constraint("fk_proposed_route_revisions_fiber_segment_id", "proposed_route_revisions", type_="foreignkey")
    op.drop_column("proposed_route_revisions", "fiber_segment_id")
