"""repair subscriber offline outreach tables

Revision ID: zl0d1e2f3a4c
Revises: zk0c1d2e3f4b
Create Date: 2026-05-20 09:20:00.000000
"""

from alembic import op

revision = "zl0d1e2f3a4c"
down_revision = "zk0c1d2e3f4b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriber_station_mappings (
            id uuid NOT NULL,
            raw_customer_base_station varchar(255) NOT NULL,
            normalized_station_key varchar(255) NOT NULL,
            monitoring_device_id varchar(120),
            monitoring_title varchar(255),
            match_method varchar(80),
            match_confidence varchar(40),
            is_manual_override boolean NOT NULL,
            notes text,
            is_active boolean NOT NULL,
            last_verified_at timestamptz,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            PRIMARY KEY (id),
            UNIQUE (raw_customer_base_station)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_subscriber_station_mappings_normalized_key
        ON subscriber_station_mappings (normalized_station_key)
        WHERE is_active IS TRUE;
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_subscriber_station_mappings_monitoring_title
        ON subscriber_station_mappings (monitoring_title)
        WHERE is_active IS TRUE;
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriber_offline_outreach_logs (
            id uuid NOT NULL,
            subscriber_id uuid REFERENCES subscribers(id),
            person_id uuid REFERENCES people(id),
            conversation_id uuid REFERENCES crm_conversations(id),
            message_id uuid REFERENCES crm_messages(id),
            channel_target_id uuid REFERENCES integration_targets(id),
            run_local_date date NOT NULL,
            external_customer_id varchar(120) NOT NULL,
            subscriber_number varchar(120),
            customer_name varchar(255),
            base_station_label varchar(255),
            normalized_station_key varchar(255),
            monitoring_device_id varchar(120),
            monitoring_title varchar(255),
            monitoring_ping_state varchar(40),
            monitoring_snmp_state varchar(40),
            station_status varchar(40),
            decision_status varchar(40) NOT NULL,
            decision_reason varchar(120),
            message_template text,
            sent_at timestamptz,
            is_active boolean NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            PRIMARY KEY (id)
        );
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_subscriber_offline_outreach_logs_run_local_date
        ON subscriber_offline_outreach_logs (run_local_date);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_subscriber_offline_outreach_logs_subscriber_run_date
        ON subscriber_offline_outreach_logs (subscriber_id, run_local_date);
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_subscriber_offline_outreach_logs_external_customer_created
        ON subscriber_offline_outreach_logs (external_customer_id, created_at);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS subscriber_offline_outreach_logs;")
    op.execute("DROP TABLE IF EXISTS subscriber_station_mappings;")
