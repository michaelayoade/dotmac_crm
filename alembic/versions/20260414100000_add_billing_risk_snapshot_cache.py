"""Add cached subscriber billing risk snapshots."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260414100000"
down_revision = "20260413123000"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "subscriber_billing_risk_snapshots" in _table_names():
        return

    bind = op.get_bind()
    uuid_type = postgresql.UUID(as_uuid=True) if bind.dialect.name == "postgresql" else sa.String(36)
    json_type = postgresql.JSONB(astext_type=sa.Text()) if bind.dialect.name == "postgresql" else sa.JSON()

    op.create_table(
        "subscriber_billing_risk_snapshots",
        sa.Column("id", uuid_type, primary_key=True, nullable=False),
        sa.Column("external_system", sa.String(length=60), nullable=False, server_default="splynx"),
        sa.Column("external_id", sa.String(length=120), nullable=False),
        sa.Column("subscriber_number", sa.String(length=60), nullable=True),
        sa.Column("person_id", uuid_type, sa.ForeignKey("people.id"), nullable=True),
        sa.Column("subscriber_id", uuid_type, sa.ForeignKey("subscribers.id"), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=120), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("area", sa.String(length=160), nullable=True),
        sa.Column("plan", sa.String(length=200), nullable=True),
        sa.Column("subscriber_status", sa.String(length=80), nullable=True),
        sa.Column("risk_segment", sa.String(length=40), nullable=False),
        sa.Column("is_high_balance_risk", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mrr_total", sa.Numeric(14, 2), nullable=True),
        sa.Column("balance", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("total_paid", sa.Numeric(14, 2), nullable=True),
        sa.Column("billing_cycle", sa.String(length=80), nullable=True),
        sa.Column("billing_start_date", sa.Date(), nullable=True),
        sa.Column("billing_end_date", sa.Date(), nullable=True),
        sa.Column("next_bill_date", sa.Date(), nullable=True),
        sa.Column("blocked_date", sa.Date(), nullable=True),
        sa.Column("last_transaction_date", sa.Date(), nullable=True),
        sa.Column("invoiced_until", sa.Date(), nullable=True),
        sa.Column("days_to_due", sa.Integer(), nullable=True),
        sa.Column("days_past_due", sa.Integer(), nullable=True),
        sa.Column("days_since_last_payment", sa.Integer(), nullable=True),
        sa.Column("blocked_for_days", sa.Integer(), nullable=True),
        sa.Column("expires_in", sa.String(length=80), nullable=True),
        sa.Column("source_metadata", json_type, nullable=True),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_billing_risk_snapshot_external",
        "subscriber_billing_risk_snapshots",
        ["external_system", "external_id"],
        unique=True,
    )
    op.create_index(
        "ix_billing_risk_snapshot_segment_balance",
        "subscriber_billing_risk_snapshots",
        ["risk_segment", "balance"],
    )
    op.create_index(
        "ix_billing_risk_snapshot_high_balance",
        "subscriber_billing_risk_snapshots",
        ["is_high_balance_risk"],
    )
    op.create_index(
        "ix_billing_risk_snapshot_days_past_due",
        "subscriber_billing_risk_snapshots",
        ["days_past_due"],
    )
    op.create_index(
        "ix_billing_risk_snapshot_blocked_for_days",
        "subscriber_billing_risk_snapshots",
        ["blocked_for_days"],
    )
    op.create_index(
        "ix_billing_risk_snapshot_refreshed_at",
        "subscriber_billing_risk_snapshots",
        ["refreshed_at"],
    )
    op.create_index(
        "ix_billing_risk_snapshot_search_name",
        "subscriber_billing_risk_snapshots",
        ["name"],
    )
    op.create_index(
        "ix_billing_risk_snapshot_search_phone",
        "subscriber_billing_risk_snapshots",
        ["phone"],
    )


def downgrade() -> None:
    if "subscriber_billing_risk_snapshots" in _table_names():
        op.drop_table("subscriber_billing_risk_snapshots")
