"""normalize existing zabbix connector row

Revision ID: zk0c1d2e3f4b
Revises: 20260515120000
Create Date: 2026-05-11 13:10:00.000000
"""

from alembic import op

revision = "zk0c1d2e3f4b"
down_revision = "20260515120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            """
            UPDATE connector_configs
            SET
                connector_type = 'zabbix',
                base_url = CASE
                    WHEN base_url ILIKE '%/zabbix/%'
                        THEN regexp_replace(base_url, '/zabbix/.*$', '/zabbix/api_jsonrpc.php')
                    ELSE base_url
                END,
                auth_type = CASE
                    WHEN auth_config IS NOT NULL
                        AND (
                            auth_config ->> 'api_key' IS NOT NULL
                            OR auth_config ->> 'token' IS NOT NULL
                            OR auth_config ->> 'bearer_token' IS NOT NULL
                        )
                        THEN 'api_key'
                    ELSE auth_type
                END
            WHERE
                lower(name) = 'zabbix'
                OR lower(name) LIKE '%zabbix%'
                OR base_url ILIKE '%/zabbix/%';
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            """
            UPDATE connector_configs
            SET connector_type = 'custom'
            WHERE connector_type = 'zabbix'
                AND (
                    lower(name) = 'zabbix'
                    OR lower(name) LIKE '%zabbix%'
                );
            """
        )
