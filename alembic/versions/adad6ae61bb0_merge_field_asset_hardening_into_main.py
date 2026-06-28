"""merge field asset hardening into main

Revision ID: adad6ae61bb0
Revises: qf7a8b9c0d1e, ld2026062900, rs2026062801, sh2026062900
Create Date: 2026-06-28 20:27:53.060738

"""

import sqlalchemy as sa
from alembic import op

revision = "adad6ae61bb0"
down_revision = ("qf7a8b9c0d1e", "ld2026062900", "rs2026062801", "sh2026062900")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
