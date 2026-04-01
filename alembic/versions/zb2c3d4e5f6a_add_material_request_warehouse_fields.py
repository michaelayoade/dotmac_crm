"""Add source/destination warehouse fields to material requests."""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "zb2c3d4e5f6a"
down_revision = "za1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "material_requests",
        sa.Column("source_location_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "material_requests",
        sa.Column("destination_location_id", sa.UUID(), nullable=True),
    )

    op.create_index(
        "ix_material_requests_source_location_id",
        "material_requests",
        ["source_location_id"],
        unique=False,
    )
    op.create_index(
        "ix_material_requests_destination_location_id",
        "material_requests",
        ["destination_location_id"],
        unique=False,
    )

    op.create_foreign_key(
        "fk_material_requests_source_location_id",
        "material_requests",
        "inventory_locations",
        ["source_location_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_material_requests_destination_location_id",
        "material_requests",
        "inventory_locations",
        ["destination_location_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_material_requests_destination_location_id", "material_requests", type_="foreignkey")
    op.drop_constraint("fk_material_requests_source_location_id", "material_requests", type_="foreignkey")

    op.drop_index("ix_material_requests_destination_location_id", table_name="material_requests")
    op.drop_index("ix_material_requests_source_location_id", table_name="material_requests")

    op.drop_column("material_requests", "destination_location_id")
    op.drop_column("material_requests", "source_location_id")
