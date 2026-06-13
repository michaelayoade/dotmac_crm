"""field-tech live location: presence snapshot + ping audit

Revision ID: a1b2c3d4e5f6
Revises: zu8f9a0b1c2d
Create Date: 2026-06-13 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a1b2c3d4e5f6"
down_revision = "zu8f9a0b1c2d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    field_presence_status = postgresql.ENUM(
        "on_shift", "on_break", "off_shift", name="fieldpresencestatus"
    )
    field_presence_status.create(bind, checkfirst=True)

    if "field_tech_presence" not in existing_tables:
        op.create_table(
            "field_tech_presence",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "status",
                postgresql.ENUM("on_shift", "on_break", "off_shift", name="fieldpresencestatus", create_type=False),
                nullable=False,
                server_default="off_shift",
            ),
            sa.Column("location_sharing_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("last_latitude", sa.Float(), nullable=True),
            sa.Column("last_longitude", sa.Float(), nullable=True),
            sa.Column("last_location_accuracy_m", sa.Float(), nullable=True),
            sa.Column("last_location_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_field_tech_presence_person_id", "field_tech_presence", ["person_id"], unique=True)
        op.create_index("ix_field_tech_presence_status", "field_tech_presence", ["status"])
        op.create_index("ix_field_tech_presence_last_location_at", "field_tech_presence", ["last_location_at"])

    if "field_tech_location_pings" not in existing_tables:
        op.create_table(
            "field_tech_location_pings",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("latitude", sa.Float(), nullable=False),
            sa.Column("longitude", sa.Float(), nullable=False),
            sa.Column("accuracy_m", sa.Float(), nullable=True),
            sa.Column("work_order_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("source", sa.String(length=32), nullable=False, server_default="mobile"),
            sa.CheckConstraint("latitude >= -90 AND latitude <= 90", name="ck_field_tech_location_pings_lat_range"),
            sa.CheckConstraint(
                "longitude >= -180 AND longitude <= 180", name="ck_field_tech_location_pings_lng_range"
            ),
            sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
            sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_field_tech_location_pings_person_received",
            "field_tech_location_pings",
            ["person_id", "received_at"],
        )
        op.create_index(
            "ix_field_tech_location_pings_received_at", "field_tech_location_pings", ["received_at"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    if "field_tech_location_pings" in existing_tables:
        op.drop_index("ix_field_tech_location_pings_received_at", table_name="field_tech_location_pings")
        op.drop_index("ix_field_tech_location_pings_person_received", table_name="field_tech_location_pings")
        op.drop_table("field_tech_location_pings")
    if "field_tech_presence" in existing_tables:
        op.drop_index("ix_field_tech_presence_last_location_at", table_name="field_tech_presence")
        op.drop_index("ix_field_tech_presence_status", table_name="field_tech_presence")
        op.drop_index("ix_field_tech_presence_person_id", table_name="field_tech_presence")
        op.drop_table("field_tech_presence")

    postgresql.ENUM(name="fieldpresencestatus").drop(bind, checkfirst=True)
