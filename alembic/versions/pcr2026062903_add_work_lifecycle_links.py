"""add work lifecycle links and outcomes

Revision ID: pcr2026062903
Revises: pcr2026062902
Create Date: 2026-06-29 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "pcr2026062903"
down_revision = "pcr2026062902"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    work_entity_type = postgresql.ENUM(
        "ticket",
        "project",
        "project_task",
        "work_order",
        "lead",
        "sales_order",
        "subscriber",
        "internal",
        name="workentitytype",
        create_type=False,
    )
    work_link_type = postgresql.ENUM(
        "originated",
        "fulfills",
        "blocks",
        "related",
        "resulted_in",
        name="worklinktype",
        create_type=False,
    )
    work_outcome_type = postgresql.ENUM(
        "no_billing_change",
        "subscriber_created",
        "subscriber_updated",
        "activation_requested",
        "repair_completed",
        "disconnect_completed",
        "custom",
        name="workoutcometype",
        create_type=False,
    )
    work_outcome_status = postgresql.ENUM(
        "pending",
        "succeeded",
        "failed",
        "reconciled",
        name="workoutcomestatus",
        create_type=False,
    )

    work_entity_type.create(bind, checkfirst=True)
    work_link_type.create(bind, checkfirst=True)
    work_outcome_type.create(bind, checkfirst=True)
    work_outcome_status.create(bind, checkfirst=True)

    if not inspector.has_table("work_links"):
        op.create_table(
            "work_links",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("source_type", work_entity_type, nullable=False),
            sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("target_type", work_entity_type, nullable=False),
            sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("link_type", work_link_type, nullable=False),
            sa.Column("contract_name", sa.String(length=120), nullable=True),
            sa.Column("created_by_person_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["created_by_person_id"], ["people.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "source_type",
                "source_id",
                "target_type",
                "target_id",
                "link_type",
                name="uq_work_links_source_target_link_type",
            ),
        )
        op.create_index("ix_work_links_source", "work_links", ["source_type", "source_id"])
        op.create_index("ix_work_links_target", "work_links", ["target_type", "target_id"])
        op.create_index("ix_work_links_contract", "work_links", ["contract_name"])

    if not inspector.has_table("work_outcomes"):
        op.create_table(
            "work_outcomes",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("work_order_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("outcome_type", work_outcome_type, nullable=False),
            sa.Column("status", work_outcome_status, nullable=False),
            sa.Column("subscriber_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("external_system", sa.String(length=60), nullable=True),
            sa.Column("external_reference", sa.String(length=120), nullable=True),
            sa.Column("idempotency_key", sa.String(length=160), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["subscriber_id"], ["subscribers.id"]),
            sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("idempotency_key", name="uq_work_outcomes_idempotency_key"),
        )
        op.create_index("ix_work_outcomes_work_order", "work_outcomes", ["work_order_id"])
        op.create_index("ix_work_outcomes_status", "work_outcomes", ["status"])
        op.create_index("ix_work_outcomes_subscriber", "work_outcomes", ["subscriber_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("work_outcomes"):
        op.drop_index("ix_work_outcomes_subscriber", table_name="work_outcomes")
        op.drop_index("ix_work_outcomes_status", table_name="work_outcomes")
        op.drop_index("ix_work_outcomes_work_order", table_name="work_outcomes")
        op.drop_table("work_outcomes")

    if inspector.has_table("work_links"):
        op.drop_index("ix_work_links_contract", table_name="work_links")
        op.drop_index("ix_work_links_target", table_name="work_links")
        op.drop_index("ix_work_links_source", table_name="work_links")
        op.drop_table("work_links")

    sa.Enum(name="workoutcomestatus").drop(bind, checkfirst=True)
    sa.Enum(name="workoutcometype").drop(bind, checkfirst=True)
    sa.Enum(name="worklinktype").drop(bind, checkfirst=True)
    sa.Enum(name="workentitytype").drop(bind, checkfirst=True)
