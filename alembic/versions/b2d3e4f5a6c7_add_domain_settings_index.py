"""Add domain_settings composite index for middleware queries

Revision ID: b2d3e4f5a6c7
Revises: c6e4b2a1d9f0
Create Date: 2026-02-02

"""
from typing import Sequence, Union

from alembic import op


revision: str = "b2d3e4f5a6c7"
down_revision: Union[str, Sequence[str], None] = "c6e4b2a1d9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create composite index for middleware queries that filter by domain + is_active
    # This eliminates sequential scans in audit_middleware and branding_middleware
    op.create_index(
        "ix_domain_settings_domain_is_active",
        "domain_settings",
        ["domain", "is_active"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_domain_settings_domain_is_active", table_name="domain_settings")
