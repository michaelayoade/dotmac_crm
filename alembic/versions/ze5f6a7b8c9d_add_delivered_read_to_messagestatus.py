"""add delivered and read to messagestatus enum"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "ze5f6a7b8c9d"
down_revision = "zd4e5f6a7b8c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE messagestatus ADD VALUE IF NOT EXISTS 'delivered' AFTER 'sent'")
    op.execute("ALTER TYPE messagestatus ADD VALUE IF NOT EXISTS 'read' AFTER 'delivered'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values; no-op.
    pass
