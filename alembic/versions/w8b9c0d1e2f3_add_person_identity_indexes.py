"""add person identity resolution indexes

Revision ID: w8b9c0d1e2f3
Revises: v7a8b9c0d1e2
Create Date: 2026-03-02 00:00:00
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "w8b9c0d1e2f3"
down_revision: str | Sequence[str] | None = "v7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_person_channels_type_address",
        "person_channels",
        ["channel_type", "address"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_people_phone",
        "people",
        ["phone"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_people_phone", table_name="people", if_exists=True)
    op.drop_index("ix_person_channels_type_address", table_name="person_channels", if_exists=True)
