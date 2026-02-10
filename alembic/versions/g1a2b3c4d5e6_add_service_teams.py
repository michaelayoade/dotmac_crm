"""add service teams

Revision ID: g1a2b3c4d5e6
Revises: f5c6d7e8f9a0
Create Date: 2026-02-10 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "g1a2b3c4d5e6"
down_revision = "f5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    service_team_type = postgresql.ENUM("operations", "support", "field_service", name="serviceteamtype", create_type=False)
    service_team_type.create(op.get_bind(), checkfirst=True)

    service_team_member_role = postgresql.ENUM("member", "lead", "manager", name="serviceteammemberrole", create_type=False)
    service_team_member_role.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "service_teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("team_type", service_team_type, nullable=False),
        sa.Column("region", sa.String(80), nullable=True),
        sa.Column("manager_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("erp_department", sa.String(120), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["manager_person_id"], ["people.id"]),
    )
    op.create_index("ix_service_teams_erp_department", "service_teams", ["erp_department"], unique=True)

    op.create_table(
        "service_team_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", service_team_member_role, nullable=False, server_default=sa.text("'member'")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["team_id"], ["service_teams.id"]),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.UniqueConstraint("team_id", "person_id", name="uq_service_team_member"),
    )
    op.create_index("ix_service_team_members_person_id", "service_team_members", ["person_id"])


def downgrade() -> None:
    op.drop_index("ix_service_team_members_person_id", table_name="service_team_members")
    op.drop_table("service_team_members")
    op.drop_index("ix_service_teams_erp_department", table_name="service_teams")
    op.drop_table("service_teams")

    op.execute("DROP TYPE IF EXISTS serviceteammemberrole")
    op.execute("DROP TYPE IF EXISTS serviceteamtype")
