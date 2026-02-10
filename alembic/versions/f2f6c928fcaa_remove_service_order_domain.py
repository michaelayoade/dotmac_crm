"""remove service order domain

Revision ID: f2f6c928fcaa
Revises: 59f402ac503f
Create Date: 2026-01-27 13:25:39.539600

"""

import sqlalchemy as sa
from alembic import op

revision = 'f2f6c928fcaa'
down_revision = '59f402ac503f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("event_store") as batch_op:
        batch_op.drop_column("service_order_id")

    with op.batch_alter_table("contract_signatures") as batch_op:
        batch_op.drop_constraint(
            "contract_signatures_service_order_id_fkey", type_="foreignkey"
        )
        batch_op.drop_column("service_order_id")

    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_constraint("projects_service_order_id_fkey", type_="foreignkey")
        batch_op.drop_column("service_order_id")

    with op.batch_alter_table("sales_orders") as batch_op:
        batch_op.drop_constraint("sales_orders_service_order_id_fkey", type_="foreignkey")
        batch_op.drop_column("service_order_id")

    with op.batch_alter_table("work_orders") as batch_op:
        batch_op.drop_constraint("work_orders_service_order_id_fkey", type_="foreignkey")
        batch_op.drop_column("service_order_id")

    op.drop_table("service_state_transitions")
    op.drop_table("provisioning_tasks")
    op.drop_table("provisioning_runs")
    op.drop_table("install_appointments")
    op.drop_table("service_orders")
    op.drop_table("provisioning_steps")
    op.drop_table("provisioning_workflows")


def downgrade() -> None:
    op.create_table(
        "provisioning_workflows",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "vendor",
            sa.Enum(
                "mikrotik",
                "huawei",
                "zte",
                "nokia",
                "genieacs",
                "other",
                name="provisioningvendor",
            ),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "provisioning_steps",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workflow_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "step_type",
            sa.Enum(
                "assign_ont",
                "push_config",
                "confirm_up",
                name="provisioningsteptype",
            ),
            nullable=False,
        ),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["provisioning_workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "service_orders",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("subscriber_id", sa.UUID(), nullable=True),
        sa.Column("requested_by_contact_id", sa.UUID(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "submitted",
                "scheduled",
                "provisioning",
                "active",
                "canceled",
                "failed",
                name="serviceorderstatus",
            ),
            nullable=False,
        ),
        sa.Column(
            "project_type",
            sa.Enum(
                "cable_rerun",
                "fiber_optics_relocation",
                "radio_fiber_relocation",
                "fiber_optics_installation",
                "radio_installation",
                name="projecttype",
            ),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["requested_by_contact_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["subscriber_id"], ["subscribers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "install_appointments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("service_order_id", sa.UUID(), nullable=False),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("technician", sa.String(length=120), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "proposed",
                "confirmed",
                "completed",
                "no_show",
                "canceled",
                name="appointmentstatus",
            ),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_self_install", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["service_order_id"], ["service_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "provisioning_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workflow_id", sa.UUID(), nullable=False),
        sa.Column("service_order_id", sa.UUID(), nullable=True),
        sa.Column("subscription_id", sa.UUID(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "success",
                "failed",
                name="provisioningrunstatus",
            ),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_payload", sa.JSON(), nullable=True),
        sa.Column("output_payload", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["service_order_id"], ["service_orders.id"]),
        sa.ForeignKeyConstraint(["workflow_id"], ["provisioning_workflows.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "provisioning_tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("service_order_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "in_progress",
                "blocked",
                "completed",
                "failed",
                name="provisioning_taskstatus",
            ),
            nullable=False,
        ),
        sa.Column("assigned_to", sa.String(length=120), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["service_order_id"], ["service_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "service_state_transitions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("service_order_id", sa.UUID(), nullable=False),
        sa.Column(
            "from_state",
            sa.Enum(
                "pending",
                "installing",
                "provisioning",
                "active",
                "suspended",
                "canceled",
                "disconnected",
                name="servicestate",
            ),
            nullable=True,
        ),
        sa.Column(
            "to_state",
            sa.Enum(
                "pending",
                "installing",
                "provisioning",
                "active",
                "suspended",
                "canceled",
                "disconnected",
                name="servicestate",
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("changed_by", sa.String(length=120), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["service_order_id"], ["service_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    with op.batch_alter_table("event_store") as batch_op:
        batch_op.add_column(sa.Column("service_order_id", sa.UUID(), nullable=True))

    with op.batch_alter_table("contract_signatures") as batch_op:
        batch_op.add_column(sa.Column("service_order_id", sa.UUID(), nullable=True))
        batch_op.create_foreign_key(
            "contract_signatures_service_order_id_fkey",
            "service_orders",
            ["service_order_id"],
            ["id"],
        )

    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(sa.Column("service_order_id", sa.UUID(), nullable=True))
        batch_op.create_foreign_key(
            "projects_service_order_id_fkey",
            "service_orders",
            ["service_order_id"],
            ["id"],
        )

    with op.batch_alter_table("sales_orders") as batch_op:
        batch_op.add_column(sa.Column("service_order_id", sa.UUID(), nullable=True))
        batch_op.create_foreign_key(
            "sales_orders_service_order_id_fkey",
            "service_orders",
            ["service_order_id"],
            ["id"],
        )

    with op.batch_alter_table("work_orders") as batch_op:
        batch_op.add_column(sa.Column("service_order_id", sa.UUID(), nullable=True))
        batch_op.create_foreign_key(
            "work_orders_service_order_id_fkey",
            "service_orders",
            ["service_order_id"],
            ["id"],
        )
