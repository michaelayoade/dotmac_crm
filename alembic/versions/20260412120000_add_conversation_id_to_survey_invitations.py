"""Add conversation_id to survey_invitations for CSAT retry."""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260412120000"
down_revision = "20260407110000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("survey_invitations")}
    if "conversation_id" not in columns:
        op.add_column(
            "survey_invitations",
            sa.Column(
                "conversation_id",
                sa.UUID(as_uuid=True) if bind.dialect.name == "postgresql" else sa.String(36),
                nullable=True,
            ),
        )
    indexes = {idx["name"] for idx in inspector.get_indexes("survey_invitations")}
    if "ix_survey_invitations_pending_retry" not in indexes:
        op.create_index(
            "ix_survey_invitations_pending_retry",
            "survey_invitations",
            ["status", "created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {idx["name"] for idx in inspector.get_indexes("survey_invitations")}
    if "ix_survey_invitations_pending_retry" in indexes:
        op.drop_index("ix_survey_invitations_pending_retry", table_name="survey_invitations")
    columns = {col["name"] for col in inspector.get_columns("survey_invitations")}
    if "conversation_id" in columns:
        op.drop_column("survey_invitations", "conversation_id")
