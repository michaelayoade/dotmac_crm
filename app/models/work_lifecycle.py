import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import relationship as orm_relationship

from app.db import Base


class WorkEntityType(enum.Enum):
    ticket = "ticket"
    project = "project"
    project_task = "project_task"
    work_order = "work_order"
    lead = "lead"
    sales_order = "sales_order"
    subscriber = "subscriber"
    internal = "internal"


class WorkLinkRelationship(enum.Enum):
    originated = "originated"
    fulfills = "fulfills"
    blocks = "blocks"
    related = "related"
    resulted_in = "resulted_in"


class WorkOutcomeType(enum.Enum):
    no_billing_change = "no_billing_change"
    subscriber_created = "subscriber_created"
    subscriber_updated = "subscriber_updated"
    activation_requested = "activation_requested"
    repair_completed = "repair_completed"
    disconnect_completed = "disconnect_completed"
    custom = "custom"


class WorkOutcomeStatus(enum.Enum):
    pending = "pending"
    succeeded = "succeeded"
    failed = "failed"
    reconciled = "reconciled"


class WorkLink(Base):
    __tablename__ = "work_links"
    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "source_id",
            "target_type",
            "target_id",
            "relationship",
            name="uq_work_links_source_target_relationship",
        ),
        Index("ix_work_links_source", "source_type", "source_id"),
        Index("ix_work_links_target", "target_type", "target_id"),
        Index("ix_work_links_contract", "contract_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_type: Mapped[WorkEntityType] = mapped_column(Enum(WorkEntityType), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    target_type: Mapped[WorkEntityType] = mapped_column(Enum(WorkEntityType), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    relationship: Mapped[WorkLinkRelationship] = mapped_column(Enum(WorkLinkRelationship), nullable=False)
    contract_name: Mapped[str | None] = mapped_column(String(120))
    created_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    created_by = orm_relationship("Person")


class WorkOutcome(Base):
    __tablename__ = "work_outcomes"
    __table_args__ = (
        Index("ix_work_outcomes_work_order", "work_order_id"),
        Index("ix_work_outcomes_status", "status"),
        Index("ix_work_outcomes_subscriber", "subscriber_id"),
        UniqueConstraint("idempotency_key", name="uq_work_outcomes_idempotency_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("work_orders.id"), nullable=False)
    outcome_type: Mapped[WorkOutcomeType] = mapped_column(Enum(WorkOutcomeType), nullable=False)
    status: Mapped[WorkOutcomeStatus] = mapped_column(Enum(WorkOutcomeStatus), default=WorkOutcomeStatus.pending)
    subscriber_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("subscribers.id"))
    external_system: Mapped[str | None] = mapped_column(String(60))
    external_reference: Mapped[str | None] = mapped_column(String(120))
    idempotency_key: Mapped[str | None] = mapped_column(String(160))
    payload: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    work_order = orm_relationship("WorkOrder", back_populates="outcomes")
    subscriber = orm_relationship("Subscriber")
