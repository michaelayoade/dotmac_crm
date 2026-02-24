"""add crm conversation macros

Revision ID: m1a2b3c4d5e6
Revises: l2a3b4c5d6e7
Create Date: 2026-02-24 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision = "m1a2b3c4d5e6"
down_revision = "l2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Create enum type (idempotent — survives partial prior runs)
    conn.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE macrovisibility AS ENUM ('personal', 'shared');
        EXCEPTION WHEN duplicate_object THEN
            NULL;
        END $$;
    """))

    # Create table only if it doesn't already exist
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS crm_conversation_macros (
            id UUID PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            description TEXT,
            visibility macrovisibility NOT NULL DEFAULT 'personal',
            created_by_agent_id UUID NOT NULL REFERENCES crm_agents(id),
            actions JSONB NOT NULL DEFAULT '[]'::jsonb,
            execution_count INTEGER NOT NULL DEFAULT 0,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        );
    """))

    # Create indexes (idempotent via IF NOT EXISTS)
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_crm_macros_visibility_active
        ON crm_conversation_macros (visibility, is_active);
    """))
    conn.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_crm_macros_agent_active
        ON crm_conversation_macros (created_by_agent_id, is_active);
    """))


def downgrade() -> None:
    op.drop_index("ix_crm_macros_agent_active", table_name="crm_conversation_macros")
    op.drop_index("ix_crm_macros_visibility_active", table_name="crm_conversation_macros")
    op.drop_table("crm_conversation_macros")
    sa.Enum(name="macrovisibility").drop(op.get_bind(), checkfirst=True)
