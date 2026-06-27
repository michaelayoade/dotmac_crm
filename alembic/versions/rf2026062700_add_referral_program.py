"""add referral program tables

Revision ID: rf2026062700
Revises: sc2026062700
Create Date: 2026-06-27

Customer-refers-customer referral program: referral_codes (one shareable code
per referrer) + referrals (attributed referral records with reward state).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "rf2026062700"
down_revision = "qb3c4d5e6f7a"
branch_labels = None
depends_on = None

referral_status = sa.Enum(
    "pending", "qualified", "rewarded", "rejected", "expired", name="referralstatus"
)
reward_status = sa.Enum(
    "none", "pending", "approved", "issued", "void", name="referralrewardstatus"
)


def upgrade() -> None:
    bind = op.get_bind()
    referral_status.create(bind, checkfirst=True)
    reward_status.create(bind, checkfirst=True)

    op.create_table(
        "referral_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("people.id"), nullable=False),
        sa.Column("code", sa.String(length=24), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_referral_codes_person_id", "referral_codes", ["person_id"])
    op.create_index("ix_referral_codes_code", "referral_codes", ["code"], unique=True)

    op.create_table(
        "referrals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("referrer_person_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("people.id"), nullable=False),
        sa.Column("referral_code_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("referral_codes.id")),
        sa.Column("referred_person_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("people.id")),
        sa.Column("referred_lead_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("crm_leads.id")),
        sa.Column("referred_subscriber_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("subscribers.id")),
        sa.Column("status", referral_status, nullable=False, server_default="pending"),
        sa.Column("reward_amount", sa.Numeric(12, 2)),
        sa.Column("reward_currency", sa.String(length=3), server_default="NGN"),
        sa.Column("reward_status", reward_status, nullable=False, server_default="none"),
        sa.Column("reward_issued_at", sa.DateTime(timezone=True)),
        sa.Column("qualified_at", sa.DateTime(timezone=True)),
        sa.Column("source", sa.String(length=40)),
        sa.Column("notes", sa.Text()),
        sa.Column("metadata", sa.JSON()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_referrals_referrer_person_id", "referrals", ["referrer_person_id"])
    op.create_index("ix_referrals_referred_person_id", "referrals", ["referred_person_id"])
    op.create_index("ix_referrals_status", "referrals", ["status"])
    op.create_index("ix_referrals_referrer", "referrals", ["referrer_person_id", "status"])
    op.create_index(
        "uq_referrals_active_referred_person",
        "referrals",
        ["referred_person_id"],
        unique=True,
        postgresql_where=sa.text("is_active AND referred_person_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("referrals")
    op.drop_table("referral_codes")
    reward_status.drop(op.get_bind(), checkfirst=True)
    referral_status.drop(op.get_bind(), checkfirst=True)
