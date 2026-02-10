"""add material requests

Revision ID: g3c4d5e6f7a8
Revises: g2b3c4d5e6f7
Create Date: 2026-02-10 00:02:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "g3c4d5e6f7a8"
down_revision = "g2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    mr_status = postgresql.ENUM(
        "draft", "submitted", "approved", "rejected", "fulfilled", "canceled",
        name="materialrequeststatus", create_type=False,
    )
    mr_status.create(op.get_bind(), checkfirst=True)

    mr_priority = postgresql.ENUM(
        "low", "medium", "high", "urgent",
        name="materialrequestpriority", create_type=False,
    )
    mr_priority.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "material_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("work_order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requested_by_person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approved_by_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", mr_status, nullable=False, server_default=sa.text("'draft'")),
        sa.Column("priority", mr_priority, nullable=False, server_default=sa.text("'medium'")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("erp_material_request_id", sa.String(120), nullable=True),
        sa.Column("number", sa.String(40), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"]),
        sa.ForeignKeyConstraint(["requested_by_person_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["approved_by_person_id"], ["people.id"]),
        sa.CheckConstraint(
            "ticket_id IS NOT NULL OR project_id IS NOT NULL",
            name="ck_material_request_has_parent",
        ),
    )
    op.create_index("ix_material_requests_ticket_id", "material_requests", ["ticket_id"])
    op.create_index("ix_material_requests_project_id", "material_requests", ["project_id"])

    op.create_table(
        "material_request_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("material_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["material_request_id"], ["material_requests.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["inventory_items.id"]),
    )


def downgrade() -> None:
    op.drop_table("material_request_items")
    op.drop_index("ix_material_requests_project_id", table_name="material_requests")
    op.drop_index("ix_material_requests_ticket_id", table_name="material_requests")
    op.drop_table("material_requests")

    op.execute("DROP TYPE IF EXISTS materialrequestpriority")
    op.execute("DROP TYPE IF EXISTS materialrequeststatus")
