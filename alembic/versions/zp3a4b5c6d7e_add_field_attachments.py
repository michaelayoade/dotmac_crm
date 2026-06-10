"""add field attachments

Revision ID: zp3a4b5c6d7e
Revises: zo2f3a4b5c6d
Create Date: 2026-06-10 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "zp3a4b5c6d7e"
down_revision = "zo2f3a4b5c6d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("field_attachments"):
        return

    field_attachment_kind = postgresql.ENUM(
        "photo",
        "signature",
        "document",
        name="fieldattachmentkind",
        create_type=False,
    )
    field_attachment_kind.create(bind, checkfirst=True)

    op.create_table(
        "field_attachments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_order_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("installation_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", field_attachment_kind, nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signer_name", sa.String(length=160), nullable=True),
        sa.Column("uploaded_by_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("uploaded_by_vendor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("client_ref", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"]),
        sa.ForeignKeyConstraint(["installation_project_id"], ["installation_projects.id"]),
        sa.ForeignKeyConstraint(["note_id"], ["work_order_notes.id"]),
        sa.ForeignKeyConstraint(["uploaded_by_person_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["uploaded_by_vendor_user_id"], ["vendor_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_ref", name="uq_field_attachments_client_ref"),
    )
    op.create_index("ix_field_attachments_client_ref", "field_attachments", ["client_ref"])
    op.create_index("ix_field_attachments_work_order", "field_attachments", ["work_order_id", "created_at"])
    op.create_index(
        "ix_field_attachments_installation_project",
        "field_attachments",
        ["installation_project_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_field_attachments_installation_project", table_name="field_attachments")
    op.drop_index("ix_field_attachments_work_order", table_name="field_attachments")
    op.drop_index("ix_field_attachments_client_ref", table_name="field_attachments")
    op.drop_table("field_attachments")
    sa.Enum(name="fieldattachmentkind").drop(op.get_bind(), checkfirst=True)
