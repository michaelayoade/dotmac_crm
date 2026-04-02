"""Add FK constraint and author columns to crm_social_comment_replies."""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "sc1a2b3c4d5e6"
down_revision = (
    "20260330095633",
    "20260330113000",
    "m3d4e5f6a7b8",
    "y4e5f6a7b8c9",
    "zd4e5f6a7b8c",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crm_social_comment_replies",
        sa.Column("author_id", sa.String(200), nullable=True),
    )
    op.add_column(
        "crm_social_comment_replies",
        sa.Column("author_name", sa.String(200), nullable=True),
    )
    op.create_foreign_key(
        "fk_crm_social_comment_replies_comment_id",
        "crm_social_comment_replies",
        "crm_social_comments",
        ["comment_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_crm_social_comment_replies_comment_id",
        "crm_social_comment_replies",
        type_="foreignkey",
    )
    op.drop_column("crm_social_comment_replies", "author_name")
    op.drop_column("crm_social_comment_replies", "author_id")
