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
    "zc3d4e5f6a7b",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {
        column["name"] for column in inspector.get_columns("crm_social_comment_replies")
    }
    if "author_id" not in existing_columns:
        op.add_column(
            "crm_social_comment_replies",
            sa.Column("author_id", sa.String(200), nullable=True),
        )
    if "author_name" not in existing_columns:
        op.add_column(
            "crm_social_comment_replies",
            sa.Column("author_name", sa.String(200), nullable=True),
        )

    existing_fk_names = {
        fk["name"]
        for fk in inspector.get_foreign_keys("crm_social_comment_replies")
        if fk.get("name")
    }
    if "fk_crm_social_comment_replies_comment_id" not in existing_fk_names:
        op.create_foreign_key(
            "fk_crm_social_comment_replies_comment_id",
            "crm_social_comment_replies",
            "crm_social_comments",
            ["comment_id"],
            ["id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_fk_names = {
        fk["name"]
        for fk in inspector.get_foreign_keys("crm_social_comment_replies")
        if fk.get("name")
    }
    if "fk_crm_social_comment_replies_comment_id" in existing_fk_names:
        op.drop_constraint(
            "fk_crm_social_comment_replies_comment_id",
            "crm_social_comment_replies",
            type_="foreignkey",
        )

    existing_columns = {
        column["name"] for column in inspector.get_columns("crm_social_comment_replies")
    }
    if "author_name" in existing_columns:
        op.drop_column("crm_social_comment_replies", "author_name")
    if "author_id" in existing_columns:
        op.drop_column("crm_social_comment_replies", "author_id")
