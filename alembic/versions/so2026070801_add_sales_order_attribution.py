"""add sales order attribution

Revision ID: so2026070801
Revises: er2026070601
Create Date: 2026-07-08 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "so2026070801"
down_revision = "er2026070601"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("sales_orders")}
    indexes = {index["name"] for index in inspector.get_indexes("sales_orders")}
    foreign_keys = {fk["name"] for fk in inspector.get_foreign_keys("sales_orders")}

    if "owner_agent_id" not in columns:
        op.add_column("sales_orders", sa.Column("owner_agent_id", postgresql.UUID(as_uuid=True), nullable=True))
    if "source" not in columns:
        op.add_column("sales_orders", sa.Column("source", sa.String(length=80), nullable=True))

    if "fk_sales_orders_owner_agent_id_crm_agents" not in foreign_keys:
        op.create_foreign_key(
            "fk_sales_orders_owner_agent_id_crm_agents",
            "sales_orders",
            "crm_agents",
            ["owner_agent_id"],
            ["id"],
        )
    if "ix_sales_orders_owner_agent_id" not in indexes:
        op.create_index("ix_sales_orders_owner_agent_id", "sales_orders", ["owner_agent_id"])
    if "ix_sales_orders_source" not in indexes:
        op.create_index("ix_sales_orders_source", "sales_orders", ["source"])

    op.execute(
        sa.text(
            """
            UPDATE sales_orders so
            SET owner_agent_id = COALESCE(so.owner_agent_id, l.owner_agent_id),
                source = COALESCE(NULLIF(so.source, ''), NULLIF(l.lead_source, ''))
            FROM crm_quotes q
            LEFT JOIN crm_leads l ON l.id = q.lead_id
            WHERE so.quote_id = q.id
              AND (so.owner_agent_id IS NULL OR so.source IS NULL OR so.source = '')
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE sales_orders so
            SET owner_agent_id = a.id
            FROM crm_quotes q
            JOIN crm_agents a ON a.person_id = q.owner_person_id
            WHERE so.quote_id = q.id
              AND so.owner_agent_id IS NULL
              AND q.owner_person_id IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {index["name"] for index in inspector.get_indexes("sales_orders")}
    foreign_keys = {fk["name"] for fk in inspector.get_foreign_keys("sales_orders")}
    columns = {column["name"] for column in inspector.get_columns("sales_orders")}

    if "ix_sales_orders_source" in indexes:
        op.drop_index("ix_sales_orders_source", table_name="sales_orders")
    if "ix_sales_orders_owner_agent_id" in indexes:
        op.drop_index("ix_sales_orders_owner_agent_id", table_name="sales_orders")
    if "fk_sales_orders_owner_agent_id_crm_agents" in foreign_keys:
        op.drop_constraint("fk_sales_orders_owner_agent_id_crm_agents", "sales_orders", type_="foreignkey")
    if "source" in columns:
        op.drop_column("sales_orders", "source")
    if "owner_agent_id" in columns:
        op.drop_column("sales_orders", "owner_agent_id")
