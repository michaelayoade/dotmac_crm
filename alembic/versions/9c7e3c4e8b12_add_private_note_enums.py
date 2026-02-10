"""add private note enum values

Revision ID: 9c7e3c4e8b12
Revises: f2f6c928fcaa
Create Date: 2026-01-28 10:15:00.000000

"""

from alembic import op

revision = "9c7e3c4e8b12"
down_revision = "f2f6c928fcaa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE channeltype ADD VALUE IF NOT EXISTS 'note'")
    op.execute("ALTER TYPE messagedirection ADD VALUE IF NOT EXISTS 'internal'")


def downgrade() -> None:
    # Clean up data that cannot be represented after enum shrink.
    op.execute("DELETE FROM crm_messages WHERE channel_type = 'note'")
    op.execute("UPDATE crm_messages SET direction = 'outbound' WHERE direction = 'internal'")

    op.execute(
        "CREATE TYPE channeltype_new AS ENUM "
        "('email', 'phone', 'sms', 'whatsapp', 'facebook_messenger', 'instagram_dm')"
    )
    op.execute("CREATE TYPE messagedirection_new AS ENUM ('inbound', 'outbound')")

    op.execute(
        "ALTER TABLE crm_routing_rules "
        "ALTER COLUMN channel_type TYPE channeltype_new "
        "USING channel_type::text::channeltype_new"
    )
    op.execute(
        "ALTER TABLE crm_team_channels "
        "ALTER COLUMN channel_type TYPE channeltype_new "
        "USING channel_type::text::channeltype_new"
    )
    op.execute(
        "ALTER TABLE person_channels "
        "ALTER COLUMN channel_type TYPE channeltype_new "
        "USING channel_type::text::channeltype_new"
    )
    op.execute(
        "ALTER TABLE crm_messages "
        "ALTER COLUMN channel_type TYPE channeltype_new "
        "USING channel_type::text::channeltype_new"
    )
    op.execute(
        "ALTER TABLE crm_messages "
        "ALTER COLUMN direction TYPE messagedirection_new "
        "USING direction::text::messagedirection_new"
    )

    op.execute("DROP TYPE channeltype")
    op.execute("ALTER TYPE channeltype_new RENAME TO channeltype")
    op.execute("DROP TYPE messagedirection")
    op.execute("ALTER TYPE messagedirection_new RENAME TO messagedirection")
