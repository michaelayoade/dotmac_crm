"""Referral program: codes per referrer + attributed referral records.

Active subscribers get a referral code; a prospect using it creates an
attributed lead; the referral qualifies (and the referrer earns an account
credit) when the referred prospect becomes an active subscriber.
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
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ReferralStatus(enum.Enum):
    pending = "pending"  # captured, awaiting qualification
    qualified = "qualified"  # referred subscriber active → reward earned
    rewarded = "rewarded"  # reward issued/applied
    rejected = "rejected"  # disqualified (self-referral, fraud, etc.)
    expired = "expired"  # qualification window passed


class ReferralRewardStatus(enum.Enum):
    none = "none"  # not yet earned
    pending = "pending"  # earned, awaiting approval
    approved = "approved"  # approved, awaiting issuance/application
    issued = "issued"  # credit applied to the referrer
    void = "void"  # cancelled


class ReferralCode(Base):
    """A unique, shareable referral code owned by one referrer (Person)."""

    __tablename__ = "referral_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(24), nullable=False, unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    person = relationship("Person", foreign_keys=[person_id])
    referrals = relationship("Referral", back_populates="code", foreign_keys="Referral.referral_code_id")


class Referral(Base):
    """One attributed referral: who referred whom, its status, and the reward."""

    __tablename__ = "referrals"
    __table_args__ = (
        Index("ix_referrals_referrer", "referrer_person_id", "status"),
        # At most one active referral per referred person (idempotent capture).
        Index(
            "uq_referrals_active_referred_person",
            "referred_person_id",
            unique=True,
            postgresql_where="is_active AND referred_person_id IS NOT NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    referrer_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), nullable=False, index=True
    )
    referral_code_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("referral_codes.id")
    )
    referred_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("people.id"), index=True
    )
    referred_lead_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_leads.id"))
    referred_subscriber_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("subscribers.id")
    )

    status: Mapped[ReferralStatus] = mapped_column(
        Enum(ReferralStatus), default=ReferralStatus.pending, nullable=False, index=True
    )
    reward_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    reward_currency: Mapped[str] = mapped_column(String(3), default="NGN")
    reward_status: Mapped[ReferralRewardStatus] = mapped_column(
        Enum(ReferralRewardStatus), default=ReferralRewardStatus.none, nullable=False
    )
    reward_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    qualified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source: Mapped[str | None] = mapped_column(String(40))
    notes: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    referrer = relationship("Person", foreign_keys=[referrer_person_id])
    referred_person = relationship("Person", foreign_keys=[referred_person_id])
    code = relationship("ReferralCode", back_populates="referrals", foreign_keys=[referral_code_id])
    lead = relationship("Lead", foreign_keys=[referred_lead_id])
    subscriber = relationship("Subscriber", foreign_keys=[referred_subscriber_id])
