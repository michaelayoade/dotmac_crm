"""add expense requests

Revision ID: er2026070601
Revises: zw0b1c2d3e4f
Create Date: 2026-07-06 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "er2026070601"
down_revision = "zw0b1c2d3e4f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_tables = set(inspector.get_table_names())

    er_status = postgresql.ENUM(
        "draft",
        "submitted",
        "approved",
        "rejected",
        "paid",
        "canceled",
        name="expenserequeststatus",
        create_type=False,
    )
    er_status.create(op.get_bind(), checkfirst=True)

    er_sync_status = postgresql.ENUM(
        "pending",
        "synced",
        "failed",
        "retrying",
        "not_configured",
        name="expenserequesterpsyncstatus",
        create_type=False,
    )
    er_sync_status.create(op.get_bind(), checkfirst=True)

    if "expense_requests" not in existing_tables:
        op.create_table(
            "expense_requests",
            sa.Column(
                "id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
            ),
            sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("work_order_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("requested_by_person_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("status", er_status, nullable=False, server_default=sa.text("'draft'")),
            sa.Column("purpose", sa.String(500), nullable=False),
            sa.Column("expense_date", sa.Date(), nullable=True),
            sa.Column("currency", sa.String(3), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("number", sa.String(40), nullable=True),
            sa.Column("rejection_reason", sa.String(500), nullable=True),
            sa.Column("erp_expense_claim_id", sa.String(120), nullable=True),
            sa.Column("erp_claim_number", sa.String(60), nullable=True),
            sa.Column("erp_claim_status", sa.String(40), nullable=True),
            sa.Column("erp_sync_status", er_sync_status, nullable=True),
            sa.Column("erp_sync_error", sa.String(500), nullable=True),
            sa.Column("erp_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("erp_sync_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"]),
            sa.ForeignKeyConstraint(["requested_by_person_id"], ["people.id"]),
        )
        op.create_index("ix_expense_requests_ticket_id", "expense_requests", ["ticket_id"])
        op.create_index("ix_expense_requests_project_id", "expense_requests", ["project_id"])
        op.create_index("ix_expense_requests_work_order_id", "expense_requests", ["work_order_id"])
        op.create_index(
            "ix_expense_requests_requested_by_person_id",
            "expense_requests",
            ["requested_by_person_id"],
        )

    if "expense_request_items" not in existing_tables:
        op.create_table(
            "expense_request_items",
            sa.Column(
                "id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
            ),
            sa.Column("expense_request_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("category_code", sa.String(30), nullable=False),
            sa.Column("category_name", sa.String(120), nullable=True),
            sa.Column("description", sa.String(500), nullable=False),
            sa.Column("amount", sa.Numeric(14, 2), nullable=False),
            sa.Column("expense_date", sa.Date(), nullable=True),
            sa.Column("vendor_name", sa.String(200), nullable=True),
            sa.Column("receipt_url", sa.String(500), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["expense_request_id"], ["expense_requests.id"]),
        )
        op.create_index(
            "ix_expense_request_items_expense_request_id",
            "expense_request_items",
            ["expense_request_id"],
        )

    _seed_permissions()


def _seed_permissions() -> None:
    """Register admin permissions for the expense request pages."""
    bind = op.get_bind()
    for key, description in (
        ("operations:expense_request:read", "View expense requests"),
        ("operations:expense_request:write", "Manage expense requests"),
    ):
        exists = bind.execute(
            sa.text("SELECT 1 FROM permissions WHERE key = :key"),
            {"key": key},
        ).first()
        if not exists:
            bind.execute(
                sa.text(
                    "INSERT INTO permissions (id, key, description, is_active, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :key, :description, true, now(), now())"
                ),
                {"key": key, "description": description},
            )


def downgrade() -> None:
    op.drop_index("ix_expense_request_items_expense_request_id", table_name="expense_request_items")
    op.drop_table("expense_request_items")
    op.drop_index("ix_expense_requests_requested_by_person_id", table_name="expense_requests")
    op.drop_index("ix_expense_requests_work_order_id", table_name="expense_requests")
    op.drop_index("ix_expense_requests_project_id", table_name="expense_requests")
    op.drop_index("ix_expense_requests_ticket_id", table_name="expense_requests")
    op.drop_table("expense_requests")

    op.execute("DROP TYPE IF EXISTS expenserequesterpsyncstatus")
    op.execute("DROP TYPE IF EXISTS expenserequeststatus")
