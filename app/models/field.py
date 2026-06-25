import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Enum, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class FieldAttachmentKind(enum.Enum):
    photo = "photo"
    signature = "signature"
    document = "document"


class FieldJobEvent(enum.Enum):
    accept = "accept"
    en_route = "en_route"
    start = "start"
    hold = "hold"
    resume = "resume"
    complete = "complete"
    # Visit ended without completion (customer absent, no access, etc.). Carries a
    # structured ``reason`` in the event payload and cancels the work order.
    unable_to_complete = "unable_to_complete"


class WorkOrderEvent(Base):
    """A field-app action on a work order, recorded as an immutable fact.

    ``client_event_id`` is unique so offline retries replay safely;
    ``occurred_at`` is the device clock, ``received_at`` the server clock —
    large deltas are flagged in ``payload`` rather than rejected.
    """

    __tablename__ = "work_order_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("work_orders.id"), nullable=False, index=True
    )
    event: Mapped[FieldJobEvent] = mapped_column(Enum(FieldJobEvent), nullable=False)
    actor_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    client_event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, index=True, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    work_order = relationship("WorkOrder")
    actor = relationship("Person", foreign_keys=[actor_person_id])


class DevicePlatform(enum.Enum):
    android = "android"
    ios = "ios"


class DeviceToken(Base):
    """FCM registration token for a mobile device.

    Owned by exactly one of person (staff technician) or vendor user — the
    service layer enforces the XOR. Tokens rotate; the unique constraint on
    ``fcm_token`` lets re-registration move a token between owners.
    """

    __tablename__ = "device_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"), index=True)
    vendor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendor_users.id"), index=True
    )
    platform: Mapped[DevicePlatform] = mapped_column(Enum(DevicePlatform), nullable=False)
    fcm_token: Mapped[str] = mapped_column(String(512), nullable=False, unique=True, index=True)
    app_version: Mapped[str | None] = mapped_column(String(40))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    person = relationship("Person", foreign_keys=[person_id])
    vendor_user = relationship("VendorUser", foreign_keys=[vendor_user_id])


class FieldAttachment(Base):
    """Evidence captured in the field (photos, signatures, documents).

    Unlike the JSON attachment lists on notes, these are first-class rows with
    GPS/timestamp metadata and a unique ``client_ref`` so offline mobile
    clients can retry uploads safely. Content is served only through the
    authenticated field API — never via the public /static mount.
    """

    __tablename__ = "field_attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("work_orders.id"))
    installation_project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("installation_projects.id")
    )
    note_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("work_order_notes.id"))
    kind: Mapped[FieldAttachmentKind] = mapped_column(
        Enum(FieldAttachmentKind), default=FieldAttachmentKind.photo, nullable=False
    )
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signer_name: Mapped[str | None] = mapped_column(String(160))
    uploaded_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    uploaded_by_vendor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendor_users.id")
    )
    client_ref: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    work_order = relationship("WorkOrder")
    installation_project = relationship("InstallationProject")
    note = relationship("WorkOrderNote")
    uploaded_by = relationship("Person", foreign_keys=[uploaded_by_person_id])
    uploaded_by_vendor_user = relationship("VendorUser", foreign_keys=[uploaded_by_vendor_user_id])
