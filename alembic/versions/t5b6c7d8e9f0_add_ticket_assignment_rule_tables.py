"""add ticket assignment rules and counters

Revision ID: t5b6c7d8e9f0
Revises: p2b3c4d5e6f8
Create Date: 2026-02-25 11:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "t5b6c7d8e9f0"
down_revision = "p2b3c4d5e6f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    strategy_enum = sa.Enum("round_robin", "least_loaded", name="ticketassignmentstrategy")
    strategy_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "ticket_assignment_rules",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("match_config", sa.JSON(), nullable=True),
        sa.Column("strategy", strategy_enum, nullable=False, server_default="round_robin"),
        sa.Column("team_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("assign_manager", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("assign_spc", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["team_id"], ["service_teams.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ticket_assignment_rules_active_priority", "ticket_assignment_rules", ["is_active", "priority"])

    op.create_table(
        "ticket_assignment_counters",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("last_assigned_person_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["last_assigned_person_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["rule_id"], ["ticket_assignment_rules.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id", name="uq_ticket_assignment_counters_rule_id"),
    )


def downgrade() -> None:
    op.drop_table("ticket_assignment_counters")
    op.drop_index("ix_ticket_assignment_rules_active_priority", table_name="ticket_assignment_rules")
    op.drop_table("ticket_assignment_rules")
    sa.Enum(name="ticketassignmentstrategy").drop(op.get_bind(), checkfirst=True)
