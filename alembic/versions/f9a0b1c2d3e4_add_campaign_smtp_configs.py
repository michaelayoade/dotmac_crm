"""Add campaign SMTP configs table and links

Revision ID: f9a0b1c2d3e4
Revises: f7a8b9c0d1e2
Create Date: 2026-02-03 12:50:00.000000

"""

revision = "f9a0b1c2d3e4"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # f7a8b9c0d1e2 already creates this table and both link columns.
    pass


def downgrade() -> None:
    pass
