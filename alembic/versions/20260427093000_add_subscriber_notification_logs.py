"""add subscriber notification logs

Revision ID: 20260427093000
Revises: zg7a8b9c0d1e
Create Date: 2026-04-27 09:30:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260427093000"
down_revision = "zg7a8b9c0d1e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    channel_enum = postgresql.ENUM(
        "email",
        "sms",
        "push",
        "whatsapp",
        "webhook",
        name="notificationchannel",
        create_type=False,
    )
    channel_enum.create(bind, checkfirst=True)

    if "subscriber_notification_logs" not in inspector.get_table_names():
        op.execute(
            """
            CREATE TABLE subscriber_notification_logs (
                id UUID NOT NULL,
                subscriber_id UUID NOT NULL,
                ticket_id UUID NULL,
                notification_id UUID NULL,
                channel notificationchannel NOT NULL,
                recipient VARCHAR(255) NOT NULL,
                message_body TEXT NOT NULL,
                scheduled_for_at TIMESTAMPTZ NOT NULL,
                sent_by_user_id UUID NULL,
                sent_by_person_id UUID NULL,
                created_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY (notification_id) REFERENCES notifications(id),
                FOREIGN KEY (sent_by_person_id) REFERENCES people(id),
                FOREIGN KEY (subscriber_id) REFERENCES subscribers(id),
                FOREIGN KEY (ticket_id) REFERENCES tickets(id)
            )
            """
        )

    indexes = {index["name"] for index in inspector.get_indexes("subscriber_notification_logs")}
    if "ix_subscriber_notification_logs_subscriber_created" not in indexes:
        op.create_index(
            "ix_subscriber_notification_logs_subscriber_created",
            "subscriber_notification_logs",
            ["subscriber_id", "created_at"],
        )

    if bind.dialect.name == "postgresql":
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_subscriber_notification_logs_notification_id "
            "ON subscriber_notification_logs (notification_id) WHERE notification_id IS NOT NULL"
        )
    else:
        op.create_index(
            "ix_subscriber_notification_logs_notification_id",
            "subscriber_notification_logs",
            ["notification_id"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_subscriber_notification_logs_notification_id")
    else:
        indexes = {index["name"] for index in inspector.get_indexes("subscriber_notification_logs")}
        if "ix_subscriber_notification_logs_notification_id" in indexes:
            op.drop_index("ix_subscriber_notification_logs_notification_id", table_name="subscriber_notification_logs")
    indexes = {index["name"] for index in inspector.get_indexes("subscriber_notification_logs")}
    if "ix_subscriber_notification_logs_subscriber_created" in indexes:
        op.drop_index("ix_subscriber_notification_logs_subscriber_created", table_name="subscriber_notification_logs")
    if "subscriber_notification_logs" in inspector.get_table_names():
        op.drop_table("subscriber_notification_logs")
