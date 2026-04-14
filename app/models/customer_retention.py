import uuid
from datetime import UTC, date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class CustomerRetentionEngagement(Base):
    __tablename__ = "customer_retention_engagements"
    __table_args__ = (
        Index("ix_customer_retention_customer_external", "customer_external_id"),
        Index("ix_customer_retention_follow_up_date", "follow_up_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_external_id: Mapped[str] = mapped_column(String(120), nullable=False)
    customer_name: Mapped[str | None] = mapped_column(String(255))
    outcome: Mapped[str] = mapped_column(String(80), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    follow_up_date: Mapped[date | None] = mapped_column(Date)
    rep_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    rep_label: Mapped[str | None] = mapped_column(String(255))
    created_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    rep = relationship("Person", foreign_keys=[rep_person_id])
    created_by = relationship("Person", foreign_keys=[created_by_person_id])
