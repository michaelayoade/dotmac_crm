"""Workqueue persistence — only snoozes."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class WorkqueueItemKind(enum.StrEnum):
    """Mirror of services.workqueue.types.ItemKind for DB storage."""

    conversation = "conversation"
    ticket = "ticket"
    lead = "lead"
    quote = "quote"
    task = "task"


class WorkqueueSnooze(Base):
    __tablename__ = "workqueue_snoozes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    item_kind: Mapped[WorkqueueItemKind] = mapped_column(
        Enum(WorkqueueItemKind, name="workqueue_item_kind", native_enum=False, length=32),
        nullable=False,
    )
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    snooze_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    until_next_reply: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "item_kind", "item_id", name="uq_workqueue_snooze_user_item"),
        Index("ix_workqueue_snooze_user_until", "user_id", "snooze_until"),
    )
