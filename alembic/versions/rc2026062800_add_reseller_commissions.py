"""add reseller commissions + payouts

Revision ID: rc2026062800
Revises: qd2026062800
Create Date: 2026-06-28

Reseller channel monetization: reseller_payouts + reseller_commissions tables,
plus organizations.commission_rate (per-reseller rate override).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "rc2026062800"
down_revision = "qd2026062800"
branch_labels = None
depends_on = None

commission_status = sa.Enum("pending", "approved", "paid", "void", name="commissionstatus")
payout_status = sa.Enum("draft", "paid", "void", name="payoutstatus")


def upgrade() -> None:
    bind = op.get_bind()
    commission_status.create(bind, checkfirst=True)
    payout_status.create(bind, checkfirst=True)

    op.add_column("organizations", sa.Column("commission_rate", sa.Numeric(5, 2), nullable=True))

    op.create_table(
        "reseller_payouts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("reseller_org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("total_amount", sa.Numeric(12, 2), server_default="0"),
        sa.Column("currency", sa.String(length=3), server_default="NGN"),
        sa.Column("status", payout_status, nullable=False, server_default="draft"),
        sa.Column("method", sa.String(length=40)),
        sa.Column("reference", sa.String(length=120)),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_reseller_payouts_reseller_org_id", "reseller_payouts", ["reseller_org_id"])

    op.create_table(
        "reseller_commissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("reseller_org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("sales_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sales_orders.id")),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("people.id")),
        sa.Column("basis_amount", sa.Numeric(12, 2), server_default="0"),
        sa.Column("rate", sa.Numeric(5, 2), server_default="0"),
        sa.Column("amount", sa.Numeric(12, 2), server_default="0"),
        sa.Column("currency", sa.String(length=3), server_default="NGN"),
        sa.Column("status", commission_status, nullable=False, server_default="pending"),
        sa.Column("payout_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("reseller_payouts.id")),
        sa.Column("earned_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text()),
        sa.Column("metadata", sa.JSON()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("sales_order_id", name="uq_reseller_commission_sales_order"),
    )
    op.create_index("ix_reseller_commissions_reseller_org_id", "reseller_commissions", ["reseller_org_id"])
    op.create_index("ix_reseller_commissions_status", "reseller_commissions", ["status"])
    op.create_index("ix_reseller_commissions_reseller", "reseller_commissions", ["reseller_org_id", "status"])


def downgrade() -> None:
    op.drop_table("reseller_commissions")
    op.drop_table("reseller_payouts")
    op.drop_column("organizations", "commission_rate")
    payout_status.drop(op.get_bind(), checkfirst=True)
    commission_status.drop(op.get_bind(), checkfirst=True)
