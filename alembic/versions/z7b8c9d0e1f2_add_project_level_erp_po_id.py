"""Add project-level ERP PO ID to installation projects."""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "z7b8c9d0e1f2"
down_revision = "z6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "installation_projects",
        sa.Column("erp_purchase_order_id", sa.String(length=100), nullable=True),
    )
    op.create_index(
        op.f("ix_installation_projects_erp_purchase_order_id"),
        "installation_projects",
        ["erp_purchase_order_id"],
        unique=False,
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT ip.id AS installation_project_id,
                   MIN(wo.metadata ->> 'erp_po_id') AS erp_po_id
            FROM installation_projects ip
            JOIN work_orders wo ON wo.project_id = ip.project_id
            WHERE wo.metadata IS NOT NULL
              AND COALESCE(wo.metadata ->> 'erp_po_id', '') <> ''
            GROUP BY ip.id
            HAVING COUNT(DISTINCT wo.metadata ->> 'erp_po_id') = 1
            """
        )
    ).fetchall()

    for row in rows:
        bind.execute(
            sa.text(
                """
                UPDATE installation_projects
                SET erp_purchase_order_id = :erp_po_id
                WHERE id = :installation_project_id
                """
            ),
            {
                "erp_po_id": row.erp_po_id,
                "installation_project_id": row.installation_project_id,
            },
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_installation_projects_erp_purchase_order_id"), table_name="installation_projects")
    op.drop_column("installation_projects", "erp_purchase_order_id")
