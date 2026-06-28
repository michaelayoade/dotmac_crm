"""Reseller channel: commissions accrued on reseller-sourced sales + payouts.

A sale is attributed to a reseller by walking the buyer organization's parent
chain to the nearest ``account_type=reseller`` org. When a reseller-sourced
sales order is paid, a pending commission accrues; approved commissions are
grouped into a payout and marked paid together.
"""

import enum
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class CommissionStatus(enum.Enum):
    pending = "pending"  # accrued, awaiting approval
    approved = "approved"  # approved, payable
    paid = "paid"  # part of a paid payout
    void = "void"  # cancelled


class PayoutStatus(enum.Enum):
    draft = "draft"
    paid = "paid"
    void = "void"


class ResellerPayout(Base):
    __tablename__ = "reseller_payouts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reseller_org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True
    )
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    currency: Mapped[str] = mapped_column(String(3), default="NGN")
    status: Mapped[PayoutStatus] = mapped_column(Enum(PayoutStatus), default=PayoutStatus.draft, nullable=False)
    method: Mapped[str | None] = mapped_column(String(40))
    reference: Mapped[str | None] = mapped_column(String(120))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    commissions = relationship(
        "ResellerCommission", back_populates="payout", foreign_keys="ResellerCommission.payout_id"
    )


class ResellerCommission(Base):
    __tablename__ = "reseller_commissions"
    __table_args__ = (
        # One commission per sales order (idempotent accrual).
        UniqueConstraint("sales_order_id", name="uq_reseller_commission_sales_order"),
        Index("ix_reseller_commissions_reseller", "reseller_org_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reseller_org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, index=True
    )
    sales_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sales_orders.id"))
    person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))

    basis_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("0.00"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    currency: Mapped[str] = mapped_column(String(3), default="NGN")
    status: Mapped[CommissionStatus] = mapped_column(
        Enum(CommissionStatus), default=CommissionStatus.pending, nullable=False, index=True
    )
    payout_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("reseller_payouts.id"))
    earned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    reseller = relationship("Organization", foreign_keys=[reseller_org_id])
    sales_order = relationship("SalesOrder", foreign_keys=[sales_order_id])
    payout = relationship("ResellerPayout", back_populates="commissions", foreign_keys=[payout_id])
