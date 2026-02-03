"""Enhance surveys with lifecycle, invitations, and analytics

Revision ID: e1f2a3b4c5d6
Revises: d8e9f0a1b2c3
Create Date: 2026-02-03 14:00:00.000000

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum types
    customer_survey_status = postgresql.ENUM(
        "draft",
        "active",
        "paused",
        "closed",
        name="customersurveystatusenum",
        create_type=False,
    )
    customer_survey_status.create(op.get_bind(), checkfirst=True)

    survey_trigger_type = postgresql.ENUM(
        "manual",
        "ticket_closed",
        "work_order_completed",
        name="surveytriggertypeenum",
        create_type=False,
    )
    survey_trigger_type.create(op.get_bind(), checkfirst=True)

    survey_invitation_status = postgresql.ENUM(
        "pending",
        "sent",
        "opened",
        "completed",
        "expired",
        name="surveyinvitationstatusenum",
        create_type=False,
    )
    survey_invitation_status.create(op.get_bind(), checkfirst=True)

    # Add columns to surveys table
    op.add_column("surveys", sa.Column("status", customer_survey_status, server_default="draft"))
    op.add_column("surveys", sa.Column("trigger_type", survey_trigger_type, server_default="manual"))
    op.add_column("surveys", sa.Column("public_slug", sa.String(120), unique=True))
    op.add_column("surveys", sa.Column("thank_you_message", sa.Text()))
    op.add_column("surveys", sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.add_column("surveys", sa.Column("segment_filter", sa.JSON()))
    op.add_column("surveys", sa.Column("created_by_id", sa.Uuid(), sa.ForeignKey("people.id")))
    op.add_column("surveys", sa.Column("total_invited", sa.Integer(), server_default="0"))
    op.add_column("surveys", sa.Column("total_responses", sa.Integer(), server_default="0"))
    op.add_column("surveys", sa.Column("avg_rating", sa.Float()))
    op.add_column("surveys", sa.Column("nps_score", sa.Float()))

    op.create_index("ix_surveys_public_slug", "surveys", ["public_slug"], unique=True)

    # Create survey_invitations table (must exist before FK reference from survey_responses)
    op.create_table(
        "survey_invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("survey_id", sa.Uuid(), sa.ForeignKey("surveys.id"), nullable=False),
        sa.Column("person_id", sa.Uuid(), sa.ForeignKey("people.id"), nullable=False),
        sa.Column("token", sa.String(64), nullable=False, unique=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("status", survey_invitation_status, server_default="pending"),
        sa.Column("notification_id", sa.Uuid(), sa.ForeignKey("notifications.id")),
        sa.Column("ticket_id", sa.Uuid(), sa.ForeignKey("tickets.id")),
        sa.Column("work_order_id", sa.Uuid(), sa.ForeignKey("work_orders.id")),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("opened_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_index("ix_survey_invitations_token", "survey_invitations", ["token"], unique=True)
    op.create_index("ix_survey_invitations_survey_id", "survey_invitations", ["survey_id"])
    op.create_unique_constraint("uq_survey_invitation_person", "survey_invitations", ["survey_id", "person_id"])

    # Add columns to survey_responses table (after survey_invitations exists for FK)
    op.add_column("survey_responses", sa.Column("invitation_id", sa.Uuid(), sa.ForeignKey("survey_invitations.id")))
    op.add_column("survey_responses", sa.Column("person_id", sa.Uuid(), sa.ForeignKey("people.id")))
    op.add_column("survey_responses", sa.Column("completed_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    # Drop FK columns from survey_responses before dropping survey_invitations table
    op.drop_column("survey_responses", "completed_at")
    op.drop_column("survey_responses", "person_id")
    op.drop_column("survey_responses", "invitation_id")

    op.drop_table("survey_invitations")

    op.drop_index("ix_surveys_public_slug", table_name="surveys")
    op.drop_column("surveys", "nps_score")
    op.drop_column("surveys", "avg_rating")
    op.drop_column("surveys", "total_responses")
    op.drop_column("surveys", "total_invited")
    op.drop_column("surveys", "created_by_id")
    op.drop_column("surveys", "segment_filter")
    op.drop_column("surveys", "expires_at")
    op.drop_column("surveys", "thank_you_message")
    op.drop_column("surveys", "public_slug")
    op.drop_column("surveys", "trigger_type")
    op.drop_column("surveys", "status")

    op.execute("DROP TYPE IF EXISTS surveyinvitationstatusenum")
    op.execute("DROP TYPE IF EXISTS surveytriggertypeenum")
    op.execute("DROP TYPE IF EXISTS customersurveystatusenum")
