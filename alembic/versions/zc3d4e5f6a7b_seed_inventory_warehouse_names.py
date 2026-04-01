"""Seed inventory warehouse names and disable placeholder UUID locations."""

import uuid

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "zc3d4e5f6a7b"
down_revision = "zb2c3d4e5f6a"
branch_labels = None
depends_on = None

WAREHOUSES: list[tuple[str, str]] = [
    ("All Warehouses - DT", "All Warehouses"),
    ("Dotmac Gudu - DT", "Dotmac Gudu"),
    ("Dotmac Gwarinpa - DT", "Dotmac Gwarinpa"),
    ("Dotmac Ikeja - DT", "Dotmac Ikeja"),
    ("Dotmac Jabi - DT", "Dotmac Jabi"),
    ("Dotmac Kubwa - DT", "Dotmac Kubwa"),
    ("Dotmac Lugbe - DT", "Dotmac Lugbe"),
    ("Dotmac Marina - DT", "Dotmac Marina"),
    ("Finished Goods - DT", "Finished Goods"),
    ("FOC- Off-Cut - DT", "FOC- Off-Cut"),
    ("Goods In Transit - DT", "Goods In Transit"),
    ("Stores - DT", "Stores Garki"),
    ("Work In Progress - DT", "Work In Progress"),
]

UUID_REGEX = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


def upgrade() -> None:
    bind = op.get_bind()

    bind.execute(
        sa.text(
            """
            UPDATE inventory_locations
            SET is_active = FALSE,
                updated_at = NOW()
            WHERE code = name
              AND code ~* :uuid_regex
              AND name ~* :uuid_regex
            """
        ),
        {"uuid_regex": UUID_REGEX},
    )

    for code, name in WAREHOUSES:
        existing_id = bind.execute(
            sa.text(
                """
                SELECT id
                FROM inventory_locations
                WHERE code = :code
                ORDER BY created_at ASC
                LIMIT 1
                """
            ),
            {"code": code},
        ).scalar()

        if existing_id:
            bind.execute(
                sa.text(
                    """
                    UPDATE inventory_locations
                    SET name = :name,
                        is_active = TRUE,
                        updated_at = NOW()
                    WHERE id = :location_id
                    """
                ),
                {"name": name, "location_id": existing_id},
            )
        else:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO inventory_locations (id, name, code, is_active, created_at, updated_at)
                    VALUES (:location_id, :name, :code, TRUE, NOW(), NOW())
                    """
                ),
                {"location_id": str(uuid.uuid4()), "name": name, "code": code},
            )


def downgrade() -> None:
    bind = op.get_bind()

    for code, name in WAREHOUSES:
        bind.execute(
            sa.text(
                """
                UPDATE inventory_locations
                SET is_active = FALSE,
                    updated_at = NOW()
                WHERE code = :code
                  AND name = :name
                """
            ),
            {"code": code, "name": name},
        )

    bind.execute(
        sa.text(
            """
            UPDATE inventory_locations
            SET is_active = TRUE,
                updated_at = NOW()
            WHERE code = name
              AND code ~* :uuid_regex
              AND name ~* :uuid_regex
            """
        ),
        {"uuid_regex": UUID_REGEX},
    )
