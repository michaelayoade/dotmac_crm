"""Add campaign management tables

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-02-03 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d8e9f0a1b2c3"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum types
    campaign_status = postgresql.ENUM(
        "draft", "scheduled", "sending", "sent", "completed", "cancelled",
        name="campaignstatus", create_type=False,
    )
    campaign_type = postgresql.ENUM(
        "one_time", "nurture",
        name="campaigntype", create_type=False,
    )
    campaign_recipient_status = postgresql.ENUM(
        "pending", "sent", "delivered", "failed", "bounced", "unsubscribed",
        name="campaignrecipientstatus", create_type=False,
    )
    campaign_status.create(op.get_bind(), checkfirst=True)
    campaign_type.create(op.get_bind(), checkfirst=True)
    campaign_recipient_status.create(op.get_bind(), checkfirst=True)

    # crm_campaigns
    op.create_table(
        "crm_campaigns",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("campaign_type", campaign_type, server_default="one_time"),
        sa.Column("status", campaign_status, server_default="draft"),
        sa.Column("subject", sa.String(200)),
        sa.Column("body_html", sa.Text),
        sa.Column("body_text", sa.Text),
        sa.Column("from_name", sa.String(160)),
        sa.Column("from_email", sa.String(255)),
        sa.Column("reply_to", sa.String(255)),
        sa.Column("segment_filter", sa.JSON),
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("sending_started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("total_recipients", sa.Integer, server_default="0"),
        sa.Column("sent_count", sa.Integer, server_default="0"),
        sa.Column("delivered_count", sa.Integer, server_default="0"),
        sa.Column("failed_count", sa.Integer, server_default="0"),
        sa.Column("opened_count", sa.Integer, server_default="0"),
        sa.Column("clicked_count", sa.Integer, server_default="0"),
        sa.Column("created_by_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("people.id")),
        sa.Column("is_active", sa.Boolean, server_default="true"),
        sa.Column("metadata", sa.JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_crm_campaigns_status", "crm_campaigns", ["status"])
    op.create_index("ix_crm_campaigns_scheduled_at", "crm_campaigns", ["scheduled_at"])
    op.create_index("ix_crm_campaigns_is_active", "crm_campaigns", ["is_active"])

    # crm_campaign_steps
    op.create_table(
        "crm_campaign_steps",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("campaign_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("crm_campaigns.id"), nullable=False),
        sa.Column("step_index", sa.Integer, server_default="0"),
        sa.Column("name", sa.String(200)),
        sa.Column("subject", sa.String(200)),
        sa.Column("body_html", sa.Text),
        sa.Column("body_text", sa.Text),
        sa.Column("delay_days", sa.Integer, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_crm_campaign_steps_campaign_id", "crm_campaign_steps", ["campaign_id"])

    # crm_campaign_recipients
    op.create_table(
        "crm_campaign_recipients",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("campaign_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("crm_campaigns.id"), nullable=False),
        sa.Column("person_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("people.id"), nullable=False),
        sa.Column("step_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("crm_campaign_steps.id")),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("status", campaign_recipient_status, server_default="pending"),
        sa.Column("notification_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("notifications.id")),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.Column("failed_reason", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_crm_campaign_recipients_campaign_id", "crm_campaign_recipients", ["campaign_id"])
    op.create_index("ix_crm_campaign_recipients_status", "crm_campaign_recipients", ["status"])
    op.create_index("ix_crm_campaign_recipients_person_id", "crm_campaign_recipients", ["person_id"])
    op.create_unique_constraint(
        "uq_campaign_person_step",
        "crm_campaign_recipients",
        ["campaign_id", "person_id", "step_id"],
    )
    # Partial unique index for NULL step_id (PostgreSQL NULL != NULL)
    op.execute(
        "CREATE UNIQUE INDEX uq_campaign_person_null_step "
        "ON crm_campaign_recipients (campaign_id, person_id) "
        "WHERE step_id IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_campaign_person_null_step")
    op.drop_table("crm_campaign_recipients")
    op.drop_table("crm_campaign_steps")
    op.drop_table("crm_campaigns")

    op.execute("DROP TYPE IF EXISTS campaignrecipientstatus")
    op.execute("DROP TYPE IF EXISTS campaigntype")
    op.execute("DROP TYPE IF EXISTS campaignstatus")
