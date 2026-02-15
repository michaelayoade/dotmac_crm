"""move talk notification settings to notification domain

Revision ID: n1a2b3c4d5f1
Revises: m1a2b3c4d5f0
Create Date: 2026-02-14 00:00:00.000000

"""

from alembic import op

revision = "n1a2b3c4d5f1"
down_revision = "m1a2b3c4d5f0"
branch_labels = None
depends_on = None

_KEYS = (
    "nextcloud_talk_notifications_enabled",
    "nextcloud_talk_notifications_base_url",
    "nextcloud_talk_notifications_username",
    "nextcloud_talk_notifications_app_password",
    "nextcloud_talk_notifications_room_type",
)


def _keys_sql() -> str:
    return ", ".join(f"'{k}'" for k in _KEYS)


def upgrade() -> None:
    keys_sql = _keys_sql()
    op.execute(
        f"""
        UPDATE domain_settings ds
        SET domain = 'notification'::settingdomain
        WHERE ds.domain = 'comms'::settingdomain
          AND ds.key IN ({keys_sql})
          AND NOT EXISTS (
              SELECT 1
              FROM domain_settings dn
              WHERE dn.domain = 'notification'::settingdomain
                AND dn.key = ds.key
          )
        """
    )
    op.execute(
        f"""
        DELETE FROM domain_settings
        WHERE domain = 'comms'::settingdomain
          AND key IN ({keys_sql})
        """
    )


def downgrade() -> None:
    keys_sql = _keys_sql()
    op.execute(
        f"""
        UPDATE domain_settings ds
        SET domain = 'comms'::settingdomain
        WHERE ds.domain = 'notification'::settingdomain
          AND ds.key IN ({keys_sql})
          AND NOT EXISTS (
              SELECT 1
              FROM domain_settings dn
              WHERE dn.domain = 'comms'::settingdomain
                AND dn.key = ds.key
          )
        """
    )
