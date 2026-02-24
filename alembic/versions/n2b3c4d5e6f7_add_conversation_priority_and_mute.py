"""add conversation priority and mute

Revision ID: n2b3c4d5e6f7
Revises: m1a2b3c4d5e6
Create Date: 2026-02-24 00:00:01.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "n2b3c4d5e6f7"
down_revision = "m1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Create the conversationpriority enum type (idempotent)
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE conversationpriority AS ENUM ('none', 'low', 'medium', 'high', 'urgent');
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END $$;
    """))

    # Add priority column if not exists
    conn.execute(sa.text("""
        ALTER TABLE crm_conversations
        ADD COLUMN IF NOT EXISTS priority conversationpriority DEFAULT 'none';
    """))

    # Add is_muted column if not exists
    conn.execute(sa.text("""
        ALTER TABLE crm_conversations
        ADD COLUMN IF NOT EXISTS is_muted BOOLEAN NOT NULL DEFAULT FALSE;
    """))

    # Add index on priority for filtering/sorting
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_crm_conversations_priority
        ON crm_conversations (priority);
    """))


def downgrade() -> None:
    op.drop_index("ix_crm_conversations_priority", table_name="crm_conversations")
    op.drop_column("crm_conversations", "is_muted")
    op.drop_column("crm_conversations", "priority")
    sa.Enum(name="conversationpriority").drop(op.get_bind(), checkfirst=True)
