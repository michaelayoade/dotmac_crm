"""Add collected_by_person_id to material requests."""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260407110000"
down_revision = ("sc1a2b3c4d5e6", "zc4d5e6f7a8b", "ze5f6a7b8c9d")
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("material_requests")}

    if "collected_by_person_id" not in existing_columns:
        op.add_column(
            "material_requests",
            sa.Column("collected_by_person_id", sa.UUID(), nullable=True),
        )

    existing_indexes = {index["name"] for index in inspector.get_indexes("material_requests")}
    if "ix_material_requests_collected_by_person_id" not in existing_indexes:
        op.create_index(
            "ix_material_requests_collected_by_person_id",
            "material_requests",
            ["collected_by_person_id"],
            unique=False,
        )

    existing_fk_names = {
        fk["name"] for fk in inspector.get_foreign_keys("material_requests") if fk.get("name")
    }
    if "fk_material_requests_collected_by_person_id" not in existing_fk_names:
        op.create_foreign_key(
            "fk_material_requests_collected_by_person_id",
            "material_requests",
            "people",
            ["collected_by_person_id"],
            ["id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_fk_names = {
        fk["name"] for fk in inspector.get_foreign_keys("material_requests") if fk.get("name")
    }
    if "fk_material_requests_collected_by_person_id" in existing_fk_names:
        op.drop_constraint(
            "fk_material_requests_collected_by_person_id",
            "material_requests",
            type_="foreignkey",
        )

    existing_indexes = {index["name"] for index in inspector.get_indexes("material_requests")}
    if "ix_material_requests_collected_by_person_id" in existing_indexes:
        op.drop_index("ix_material_requests_collected_by_person_id", table_name="material_requests")

    existing_columns = {column["name"] for column in inspector.get_columns("material_requests")}
    if "collected_by_person_id" in existing_columns:
        op.drop_column("material_requests", "collected_by_person_id")
