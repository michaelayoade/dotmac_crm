from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.field import DevicePlatform, FieldAttachmentKind


class FieldAttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_order_id: UUID | None
    installation_project_id: UUID | None
    note_id: UUID | None
    kind: FieldAttachmentKind
    file_name: str
    mime_type: str
    size_bytes: int
    latitude: float | None
    longitude: float | None
    captured_at: datetime | None
    signer_name: str | None
    uploaded_by_person_id: UUID | None
    uploaded_by_vendor_user_id: UUID | None
    client_ref: UUID | None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def download_path(self) -> str:
        return f"/api/v1/field/attachments/{self.id}/content"


class DeviceTokenRegister(BaseModel):
    platform: str = Field(min_length=1, max_length=20)
    fcm_token: str = Field(min_length=1, max_length=512)
    app_version: str | None = Field(default=None, max_length=40)


class DeviceTokenRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    person_id: UUID | None
    vendor_user_id: UUID | None
    platform: DevicePlatform
    app_version: str | None
    last_seen_at: datetime | None
    created_at: datetime


class FieldMeResponse(BaseModel):
    person_id: UUID
    name: str
    email: str | None
    technician_title: str | None
    region: str | None
    open_jobs: int
    completed_today: int


class FieldJobSummary(BaseModel):
    """Work order summary for the technician job list. No cost fields."""

    id: UUID
    title: str
    description: str | None
    status: str
    priority: str
    work_type: str
    scheduled_start: datetime | None
    scheduled_end: datetime | None
    estimated_duration_minutes: int | None
    estimated_arrival_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None

    @classmethod
    def from_work_order(cls, work_order) -> FieldJobSummary:
        return cls(
            id=work_order.id,
            title=work_order.title,
            description=work_order.description,
            status=work_order.status.value,
            priority=work_order.priority.value,
            work_type=work_order.work_type.value,
            scheduled_start=work_order.scheduled_start,
            scheduled_end=work_order.scheduled_end,
            estimated_duration_minutes=work_order.estimated_duration_minutes,
            estimated_arrival_at=work_order.estimated_arrival_at,
            started_at=work_order.started_at,
            completed_at=work_order.completed_at,
        )


class FieldCustomer(BaseModel):
    subscriber_id: UUID
    name: str | None
    phone: str | None
    email: str | None
    address_text: str | None
    service_plan: str | None
    account_number: str | None
    status: str | None


class FieldNoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    body: str
    is_internal: bool
    author_person_id: UUID | None
    created_at: datetime


class FieldNoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10000)
    attachment_ids: list[UUID] = Field(default_factory=list, max_length=20)


class FieldMaterialRead(BaseModel):
    id: UUID
    item_name: str | None
    item_sku: str | None
    quantity: int
    consumed_quantity: int
    status: str

    @classmethod
    def from_material(cls, material) -> FieldMaterialRead:
        return cls(
            id=material.id,
            item_name=material.item.name if material.item else None,
            item_sku=getattr(material.item, "sku", None) if material.item else None,
            quantity=material.quantity,
            consumed_quantity=material.consumed_quantity,
            status=material.status.value,
        )


class FieldMaterialConsumeItem(BaseModel):
    material_id: UUID
    consumed_quantity: int = Field(ge=0)
    leftover_note: str | None = Field(default=None, max_length=500)


class FieldMaterialConsumeRequest(BaseModel):
    items: list[FieldMaterialConsumeItem] = Field(min_length=1, max_length=100)


class FieldWorkLogRead(BaseModel):
    """Worklog view for technicians — rates and costs are intentionally absent."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    person_id: UUID
    start_at: datetime
    end_at: datetime | None
    minutes: int
    notes: str | None


class FieldJobLocation(BaseModel):
    latitude: float | None
    longitude: float | None
    address_text: str | None
    source: str  # cached | geocoded | address_only | none


class FieldJobDetail(BaseModel):
    job: FieldJobSummary
    customer: FieldCustomer | None
    location: FieldJobLocation
    ticket_ref: str | None
    project_id: UUID | None
    notes: list[FieldNoteRead]
    attachments: list[FieldAttachmentRead]
    materials: list[FieldMaterialRead]
    worklogs: list[FieldWorkLogRead]


class FieldTransitionRequest(BaseModel):
    event: str = Field(min_length=1, max_length=20)
    client_event_id: UUID
    occurred_at: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    note: str | None = Field(default=None, max_length=2000)
    payload: dict | None = None


class FieldTransitionResponse(BaseModel):
    job: FieldJobSummary
    event: str
    event_id: UUID
    replayed: bool


class FieldWorkLogEntry(BaseModel):
    start_at: datetime
    end_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)


class FieldWorkLogSubmit(BaseModel):
    entries: list[FieldWorkLogEntry] = Field(min_length=1, max_length=50)


class FieldWorkLogResult(BaseModel):
    worklog: FieldWorkLogRead
    duplicate: bool
    backdated: bool


class FieldWorkLogSubmitResponse(BaseModel):
    results: list[FieldWorkLogResult]


class FieldEquipmentRecord(BaseModel):
    serial_number: str = Field(min_length=1, max_length=120)
    vendor: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)


class FieldEquipmentRead(BaseModel):
    assignment_id: UUID
    serial_number: str
    vendor: str | None
    model: str | None
    assigned_at: datetime | None
    active: bool

    @classmethod
    def from_assignment(cls, assignment) -> FieldEquipmentRead:
        return cls(
            assignment_id=assignment.id,
            serial_number=assignment.ont_unit.serial_number,
            vendor=assignment.ont_unit.vendor,
            model=assignment.ont_unit.model,
            assigned_at=assignment.assigned_at,
            active=assignment.active,
        )


# ---------------------------------------------------------------------------
# Live location (Phase 3)
# ---------------------------------------------------------------------------


class LocationPingInput(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)
    captured_at: datetime | None = None
    work_order_id: UUID | None = None
    source: str = Field(default="mobile", max_length=32)
    status: str | None = Field(default=None, max_length=20)


class LocationPingBatch(BaseModel):
    pings: list[LocationPingInput] = Field(min_length=1, max_length=200)


class LocationSharingUpdate(BaseModel):
    enabled: bool
    status: str | None = Field(default=None, max_length=20)


class FieldPresenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    person_id: UUID
    status: str
    location_sharing_enabled: bool
    last_latitude: float | None
    last_longitude: float | None
    last_location_accuracy_m: float | None
    last_location_at: datetime | None
    last_seen_at: datetime | None

    @classmethod
    def from_presence(cls, presence) -> FieldPresenceRead:
        return cls(
            person_id=presence.person_id,
            status=presence.status.value,
            location_sharing_enabled=presence.location_sharing_enabled,
            last_latitude=presence.last_latitude,
            last_longitude=presence.last_longitude,
            last_location_accuracy_m=presence.last_location_accuracy_m,
            last_location_at=presence.last_location_at,
            last_seen_at=presence.last_seen_at,
        )


class LocationIngestResponse(BaseModel):
    accepted: int
    errors: list[dict] = Field(default_factory=list)
    presence: FieldPresenceRead
    # Geofence auto-transitions triggered by this batch (task #46).
    transitions: list[dict] = Field(default_factory=list)
