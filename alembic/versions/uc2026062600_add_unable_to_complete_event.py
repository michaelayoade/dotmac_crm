"""Add unable_to_complete to the fieldjobevent enum."""

from alembic import op

# revision identifiers, used by Alembic.
revision = "uc2026062600"
down_revision = "wl2026062500"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE fieldjobevent ADD VALUE IF NOT EXISTS 'unable_to_complete'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values in-place.
    pass
