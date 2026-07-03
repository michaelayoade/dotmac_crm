from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.field import DevicePlatform, FieldAttachmentKind
from app.models.material_request import MaterialRequestPriority
from app.models.sales_order import SalesOrderPaymentStatus, SalesOrderStatus
from app.schemas.material_request import MaterialRequestItemCreate


class FieldAttachmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_order_id: UUID | None
    installation_project_id: UUID | None
    note_id: UUID | None
    asset_type: str | None = None
    asset_id: UUID | None = None
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
    author_name: str | None = None

    @classmethod
    def from_note(cls, note) -> FieldNoteRead:
        return cls.model_validate(note).model_copy(update={"author_name": _person_label(getattr(note, "author", None))})


def _person_label(person) -> str | None:
    if person is None:
        return None
    display_name = getattr(person, "display_name", None)
    if isinstance(display_name, str) and display_name.strip():
        return display_name.strip()
    name = " ".join(
        part
        for part in [
            getattr(person, "first_name", None),
            getattr(person, "last_name", None),
        ]
        if isinstance(part, str) and part.strip()
    )
    return name or None


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


class FieldMaterialRequestCreate(BaseModel):
    ticket_id: UUID | None = None
    project_id: UUID | None = None
    work_order_id: UUID | None = None
    priority: MaterialRequestPriority = MaterialRequestPriority.medium
    notes: str | None = Field(default=None, max_length=5000)
    source_location_id: UUID | None = None
    destination_location_id: UUID | None = None
    items: list[MaterialRequestItemCreate] = Field(default_factory=list, max_length=100)
    submit: bool = True


class FieldCustomerSearchItem(BaseModel):
    id: UUID
    type: Literal["person"]
    label: str
    ref: str
    email: str | None = None
    phone: str | None = None
    address_text: str | None = None
    account_status: str | None = None
    service_plan: str | None = None
    recent_jobs: list[dict] = Field(default_factory=list)
    recent_tickets: list[dict] = Field(default_factory=list)


class FieldSalesOrderLineCreate(BaseModel):
    inventory_item_id: UUID | None = None
    description: str = Field(min_length=1, max_length=255)
    quantity: Decimal = Field(default=Decimal("1.000"), gt=0)
    unit_price: Decimal = Field(default=Decimal("0.00"), ge=0)


class FieldSalesOrderCreate(BaseModel):
    person_id: UUID
    notes: str | None = Field(default=None, max_length=5000)
    currency: str = Field(default="NGN", min_length=3, max_length=3)
    lines: list[FieldSalesOrderLineCreate] = Field(min_length=1, max_length=100)


class FieldSalesOrderLineRead(BaseModel):
    id: UUID
    inventory_item_id: UUID | None
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal


class FieldSalesOrderRead(BaseModel):
    id: UUID
    person_id: UUID
    order_number: str | None
    status: SalesOrderStatus
    payment_status: SalesOrderPaymentStatus
    currency: str
    subtotal: Decimal
    total: Decimal
    balance_due: Decimal
    notes: str | None
    created_at: datetime
    lines: list[FieldSalesOrderLineRead] = Field(default_factory=list)

    @classmethod
    def from_order(cls, order) -> FieldSalesOrderRead:
        return cls(
            id=order.id,
            person_id=order.person_id,
            order_number=order.order_number,
            status=order.status,
            payment_status=order.payment_status,
            currency=order.currency,
            subtotal=order.subtotal,
            total=order.total,
            balance_due=order.balance_due,
            notes=order.notes,
            created_at=order.created_at,
            lines=[
                FieldSalesOrderLineRead(
                    id=line.id,
                    inventory_item_id=line.inventory_item_id,
                    description=line.description,
                    quantity=line.quantity,
                    unit_price=line.unit_price,
                    amount=line.amount,
                )
                for line in (order.lines or [])
                if line.is_active
            ],
        )


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
    source: str  # cached | geocoded | manual | address_only | none


class FieldJobLocationUpdate(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class FieldMapAsset(BaseModel):
    id: UUID
    type: str
    title: str
    subtitle: str | None = None
    latitude: float
    longitude: float
    status: str | None = None
    updated_at: datetime | None = None


class FieldMapAssetDeleted(BaseModel):
    type: str
    id: UUID
    deleted_at: datetime


class FieldMapAssetListResponse(BaseModel):
    items: list[FieldMapAsset]
    deleted: list[FieldMapAssetDeleted] = Field(default_factory=list)
    count: int
    limit: int
    offset: int
    server_time: datetime


class FieldMapAssetNearby(FieldMapAsset):
    distance_m: float


class FieldMapAssetNearbyResponse(BaseModel):
    items: list[FieldMapAssetNearby]
    count: int
    latitude: float
    longitude: float
    radius_m: float
    server_time: datetime


class FieldMapSearchResult(BaseModel):
    kind: Literal["job", "asset"]
    id: UUID
    asset_type: str | None = None
    title: str
    subtitle: str | None = None
    latitude: float
    longitude: float
    status: str | None = None
    address_text: str | None = None


class FieldMapSearchResponse(BaseModel):
    items: list[FieldMapSearchResult]
    count: int
    limit: int
    offset: int = 0


class FieldSpliceCreate(BaseModel):
    closure_id: UUID
    from_strand_id: UUID
    to_strand_id: UUID
    tray_id: UUID | None = None
    position: int | None = Field(default=None, ge=1)
    splice_type: str | None = Field(default=None, max_length=80)
    # Splice loss in dB. Fusion splices are typically <0.3 dB; reject values
    # outside a plausible field band as fat-finger entry.
    loss_db: float | None = Field(default=None, ge=0, le=5)
    note: str | None = Field(default=None, max_length=2000)


class FieldSpliceProposalResponse(BaseModel):
    change_request_id: UUID
    status: str
    replayed: bool
    closure_id: UUID
    from_strand_id: UUID
    to_strand_id: UUID


class FieldFiberTestCreate(BaseModel):
    work_order_id: UUID
    asset_type: str = Field(min_length=1, max_length=80)
    asset_id: UUID
    test_type: str = Field(min_length=1, max_length=40)
    wavelength_nm: int | None = Field(default=None, ge=0)
    value_db: float | None = None
    unit: str | None = Field(default=None, max_length=16)
    passed: bool | None = None
    instrument: str | None = Field(default=None, max_length=120)
    measured_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)
    attachment_id: UUID | None = None
    client_ref: UUID | None = None


class FieldFiberTestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_order_id: UUID | None
    asset_type: str
    asset_id: UUID
    test_type: str
    wavelength_nm: int | None
    value_db: float | None
    unit: str | None
    passed: bool | None
    instrument: str | None
    attachment_id: UUID | None
    measured_by_person_id: UUID | None
    measured_at: datetime | None
    notes: str | None
    created_at: datetime


class FieldMapAssetLocationUpdate(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    # Optimistic concurrency token: the ``updated_at`` the device last saw for
    # this asset. When present and stale, the server rejects with 409 instead of
    # letting a delayed offline edit clobber a newer correction.
    expected_updated_at: datetime | None = None
    # Pin provenance, recorded on the audit trail (a 50m-accuracy phone fix and a
    # surveyed coordinate should not look identical after the fact).
    source: str | None = Field(default=None, max_length=32)
    accuracy_m: float | None = Field(default=None, ge=0)
    # Offline idempotency key so retried uploads are traceable to one capture.
    client_ref: UUID | None = None
    # Override the downgrade guard (e.g. knowingly replacing a surveyed point
    # with a fresh field fix).
    force: bool = False
    # Intent: a correction fixes a wrong pin (no downstream impact); a relocation
    # means the asset physically moved, so connected segments are flagged stale.
    move_type: Literal["correction", "relocation"] | None = None


class FieldSiteContact(BaseModel):
    """An additional account contact the tech can reach on site."""

    name: str | None
    phone: str | None
    email: str | None
    relationship: str | None = None


class FieldVisitHistoryItem(BaseModel):
    work_order_id: UUID
    title: str
    work_type: str | None
    status: str | None
    completed_at: datetime | None


class FieldOpenTicketItem(BaseModel):
    id: UUID
    ref: str | None
    subject: str | None
    status: str | None


class FieldJobDetail(BaseModel):
    job: FieldJobSummary
    customer: FieldCustomer | None
    location: FieldJobLocation
    ticket_ref: str | None
    project_id: UUID | None
    access_notes: str | None = None
    additional_contacts: list[FieldSiteContact] = []
    recent_visits: list[FieldVisitHistoryItem] = []
    open_tickets: list[FieldOpenTicketItem] = []
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
    # Per-entry idempotency key so retried offline uploads dedupe server-side.
    client_ref: UUID | None = None


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


# ---------------------------------------------------------------------------
# Voice capture → structured field data (Phase 3, tasks #48/#49/#50)
# ---------------------------------------------------------------------------


class VoiceExtractRequest(BaseModel):
    transcript: str = Field(min_length=1, max_length=4000)
    context: str | None = Field(default=None, max_length=120)
    # ASR-reported confidence, if the on-device transcriber provides one.
    asr_confidence: float | None = Field(default=None, ge=0, le=1)


class VoiceExtractResponse(BaseModel):
    work_status: str | None
    equipment_serial: str | None
    signal_readings: dict
    materials_used: list[dict]
    notes: str
    confidence: float | None
    # Quality gate (task #50): when true the tech must confirm before saving.
    requires_review: bool
    review_reasons: list[str] = Field(default_factory=list)
