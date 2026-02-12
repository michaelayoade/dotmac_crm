import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class MaterialRequestStatus(enum.Enum):
    draft = "draft"
    submitted = "submitted"
    issued = "issued"
    approved = "approved"
    rejected = "rejected"
    fulfilled = "fulfilled"
    canceled = "canceled"


class MaterialRequestPriority(enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    urgent = "urgent"


class MaterialRequest(Base):
    __tablename__ = "material_requests"
    __table_args__ = (
        CheckConstraint(
            "ticket_id IS NOT NULL OR project_id IS NOT NULL",
            name="ck_material_request_has_parent",
        ),
        Index("ix_material_requests_ticket_id", "ticket_id"),
        Index("ix_material_requests_project_id", "project_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"))
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("work_orders.id"))
    requested_by_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=False
    )
    approved_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    status: Mapped[MaterialRequestStatus] = mapped_column(
        Enum(MaterialRequestStatus), default=MaterialRequestStatus.draft
    )
    priority: Mapped[MaterialRequestPriority] = mapped_column(
        Enum(MaterialRequestPriority), default=MaterialRequestPriority.medium
    )
    notes: Mapped[str | None] = mapped_column(Text)
    erp_material_request_id: Mapped[str | None] = mapped_column(String(120))
    number: Mapped[str | None] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    ticket = relationship("Ticket", foreign_keys=[ticket_id])
    project = relationship("Project", foreign_keys=[project_id])
    work_order = relationship("WorkOrder", foreign_keys=[work_order_id])
    requested_by = relationship("Person", foreign_keys=[requested_by_person_id])
    approved_by = relationship("Person", foreign_keys=[approved_by_person_id])
    items = relationship("MaterialRequestItem", back_populates="material_request", cascade="all, delete-orphan")


class MaterialRequestItem(Base):
    __tablename__ = "material_request_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    material_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("material_requests.id"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("inventory_items.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    material_request = relationship("MaterialRequest", back_populates="items")
    item = relationship("InventoryItem", foreign_keys=[item_id])
