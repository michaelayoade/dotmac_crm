"""allow csat invites per conversation

Revision ID: zg7a8b9c0d1e
Revises: zf6a7b8c9d0e
Create Date: 2026-04-23 00:00:00.000000
"""

from alembic import op

revision = "zg7a8b9c0d1e"
down_revision = "zf6a7b8c9d0e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_survey_invitation_person", "survey_invitations", type_="unique")
    op.create_index(
        "ix_survey_invitations_survey_person_conversation",
        "survey_invitations",
        ["survey_id", "person_id", "conversation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_survey_invitations_survey_person_conversation", table_name="survey_invitations")
    op.create_unique_constraint(
        "uq_survey_invitation_person",
        "survey_invitations",
        ["survey_id", "person_id"],
    )
