import enum
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ExpenseRequestStatus(enum.Enum):
    draft = "draft"
    submitted = "submitted"
    approved = "approved"
    rejected = "rejected"
    paid = "paid"
    canceled = "canceled"


class ExpenseRequestERPSyncStatus(enum.Enum):
    pending = "pending"
    synced = "synced"
    failed = "failed"
    retrying = "retrying"
    not_configured = "not_configured"


class ExpenseRequest(Base):
    """A field expense request; approval and payment happen in DotMac ERP."""

    __tablename__ = "expense_requests"
    __table_args__ = (
        Index("ix_expense_requests_ticket_id", "ticket_id"),
        Index("ix_expense_requests_project_id", "project_id"),
        Index("ix_expense_requests_work_order_id", "work_order_id"),
        Index("ix_expense_requests_requested_by_person_id", "requested_by_person_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("projects.id"))
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("work_orders.id"))
    requested_by_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=False
    )
    status: Mapped[ExpenseRequestStatus] = mapped_column(Enum(ExpenseRequestStatus), default=ExpenseRequestStatus.draft)
    purpose: Mapped[str] = mapped_column(String(500), nullable=False)
    expense_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(3))
    notes: Mapped[str | None] = mapped_column(Text)
    number: Mapped[str | None] = mapped_column(String(40))
    rejection_reason: Mapped[str | None] = mapped_column(String(500))

    erp_expense_claim_id: Mapped[str | None] = mapped_column(String(120))
    erp_claim_number: Mapped[str | None] = mapped_column(String(60))
    erp_claim_status: Mapped[str | None] = mapped_column(String(40))
    erp_sync_status: Mapped[ExpenseRequestERPSyncStatus | None] = mapped_column(Enum(ExpenseRequestERPSyncStatus))
    erp_sync_error: Mapped[str | None] = mapped_column(String(500))
    erp_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    erp_sync_attempts: Mapped[int] = mapped_column(Integer, default=0)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
    items = relationship("ExpenseRequestItem", back_populates="expense_request", cascade="all, delete-orphan")

    @property
    def total_amount(self) -> Decimal:
        return sum((item.amount for item in self.items), Decimal("0"))


class ExpenseRequestItem(Base):
    __tablename__ = "expense_request_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    expense_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expense_requests.id"), nullable=False
    )
    category_code: Mapped[str] = mapped_column(String(30), nullable=False)
    category_name: Mapped[str | None] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    expense_date: Mapped[date | None] = mapped_column(Date)
    vendor_name: Mapped[str | None] = mapped_column(String(200))
    receipt_url: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    expense_request = relationship("ExpenseRequest", back_populates="items")
