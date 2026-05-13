"""add subscriber offline outreach tables

Revision ID: zi9b0c1d2e3f
Revises: zh8a9b0c1d2e
Create Date: 2026-05-11 13:10:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "zi9b0c1d2e3f"
down_revision = "zh8a9b0c1d2e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subscriber_station_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_customer_base_station", sa.String(length=255), nullable=False),
        sa.Column("normalized_station_key", sa.String(length=255), nullable=False),
        sa.Column("monitoring_device_id", sa.String(length=120), nullable=True),
        sa.Column("monitoring_title", sa.String(length=255), nullable=True),
        sa.Column("match_method", sa.String(length=80), nullable=True),
        sa.Column("match_confidence", sa.String(length=40), nullable=True),
        sa.Column("is_manual_override", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("raw_customer_base_station"),
    )
    op.create_index(
        "ix_subscriber_station_mappings_normalized_key",
        "subscriber_station_mappings",
        ["normalized_station_key"],
        postgresql_where=sa.text("is_active IS TRUE"),
    )
    op.create_index(
        "ix_subscriber_station_mappings_monitoring_title",
        "subscriber_station_mappings",
        ["monitoring_title"],
        postgresql_where=sa.text("is_active IS TRUE"),
    )

    op.create_table(
        "subscriber_offline_outreach_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subscriber_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel_target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_local_date", sa.Date(), nullable=False),
        sa.Column("external_customer_id", sa.String(length=120), nullable=False),
        sa.Column("subscriber_number", sa.String(length=120), nullable=True),
        sa.Column("customer_name", sa.String(length=255), nullable=True),
        sa.Column("base_station_label", sa.String(length=255), nullable=True),
        sa.Column("normalized_station_key", sa.String(length=255), nullable=True),
        sa.Column("monitoring_device_id", sa.String(length=120), nullable=True),
        sa.Column("monitoring_title", sa.String(length=255), nullable=True),
        sa.Column("monitoring_ping_state", sa.String(length=40), nullable=True),
        sa.Column("monitoring_snmp_state", sa.String(length=40), nullable=True),
        sa.Column("station_status", sa.String(length=40), nullable=True),
        sa.Column("decision_status", sa.String(length=40), nullable=False),
        sa.Column("decision_reason", sa.String(length=120), nullable=True),
        sa.Column("message_template", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["channel_target_id"], ["integration_targets.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["crm_conversations.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["crm_messages.id"]),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["subscriber_id"], ["subscribers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_subscriber_offline_outreach_logs_run_local_date",
        "subscriber_offline_outreach_logs",
        ["run_local_date"],
    )
    op.create_index(
        "ix_subscriber_offline_outreach_logs_subscriber_run_date",
        "subscriber_offline_outreach_logs",
        ["subscriber_id", "run_local_date"],
    )
    op.create_index(
        "ix_subscriber_offline_outreach_logs_external_customer_created",
        "subscriber_offline_outreach_logs",
        ["external_customer_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_subscriber_offline_outreach_logs_external_customer_created",
        table_name="subscriber_offline_outreach_logs",
    )
    op.drop_index(
        "ix_subscriber_offline_outreach_logs_subscriber_run_date",
        table_name="subscriber_offline_outreach_logs",
    )
    op.drop_index(
        "ix_subscriber_offline_outreach_logs_run_local_date",
        table_name="subscriber_offline_outreach_logs",
    )
    op.drop_table("subscriber_offline_outreach_logs")

    op.drop_index(
        "ix_subscriber_station_mappings_monitoring_title",
        table_name="subscriber_station_mappings",
    )
    op.drop_index(
        "ix_subscriber_station_mappings_normalized_key",
        table_name="subscriber_station_mappings",
    )
    op.drop_table("subscriber_station_mappings")
