"""Add ERPNext entity types to ExternalEntityType enum.

Revision ID: 2a4ea51a31ac
Revises: 
Create Date: 2026-01-31

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = '2a4ea51a31ac'
down_revision = 'd1e2f3a4b5c6'
branch_labels = None
depends_on = None


def upgrade():
    # Add new values to the externalentitytype enum
    op.execute("ALTER TYPE externalentitytype ADD VALUE IF NOT EXISTS 'person'")
    op.execute("ALTER TYPE externalentitytype ADD VALUE IF NOT EXISTS 'subscriber'")
    op.execute("ALTER TYPE externalentitytype ADD VALUE IF NOT EXISTS 'lead'")
    op.execute("ALTER TYPE externalentitytype ADD VALUE IF NOT EXISTS 'quote'")


def downgrade():
    # PostgreSQL doesn't support removing enum values easily
    # Would need to recreate the type, which is complex
    pass
