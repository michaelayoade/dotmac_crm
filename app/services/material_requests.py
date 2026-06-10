from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload

from app.models.domain_settings import SettingDomain
from app.models.inventory import InventoryItem, InventoryLocation
from app.models.material_request import (
    MaterialRequest,
    MaterialRequestERPSyncStatus,
    MaterialRequestItem,
    MaterialRequestPriority,
    MaterialRequestStatus,
)
from app.models.person import Person
from app.schemas.material_request import (
    MaterialRequestCreate,
    MaterialRequestItemCreate,
    MaterialRequestUpdate,
)
from app.services.common import (
    apply_is_active_filter,
    apply_ordering,
    apply_pagination,
    coerce_uuid,
    get_or_404,
    validate_enum,
)
from app.services.numbering import generate_number
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


# Terminal statuses that cannot transition further
class ResolveError(Exception):
    """Raised when a user-supplied reference (ticket, project, warehouse) can't be resolved."""


def resolve_ticket_id(db: Session, value: str | None):
    """Resolve a ticket number or UUID string to a ticket UUID. Returns None if empty."""
    from app.models.tickets import Ticket

    raw = (value or "").strip()
    if not raw:
        return None
    ticket = db.query(Ticket).filter(Ticket.number == raw).first()
    if ticket:
        return ticket.id
    try:
        return coerce_uuid(raw)
    except (ValueError, AttributeError):
        raise ResolveError(f"Ticket '{raw}' not found")


def resolve_project_id(db: Session, value: str | None):
    """Resolve a project number or UUID string to a project UUID. Returns None if empty."""
    from app.models.projects import Project

    raw = (value or "").strip()
    if not raw:
        return None
    project = db.query(Project).filter(Project.number == raw).first()
    if project:
        return project.id
    try:
        return coerce_uuid(raw)
    except (ValueError, AttributeError):
        raise ResolveError(f"Project '{raw}' not found")


def resolve_warehouse_id(value: str | None):
    """Resolve a warehouse UUID string. Returns None if empty."""
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return coerce_uuid(raw)
    except (ValueError, AttributeError):
        raise ResolveError(f"Warehouse '{raw}' is not a valid ID")


_TERMINAL_STATUSES = {
    MaterialRequestStatus.issued,
    MaterialRequestStatus.approved,
    MaterialRequestStatus.fulfilled,
    MaterialRequestStatus.rejected,
    MaterialRequestStatus.canceled,
}


def _enqueue_erp_sync(db: Session, mr: MaterialRequest) -> MaterialRequest:
    mr.erp_sync_status = MaterialRequestERPSyncStatus.pending
    mr.erp_sync_error = None
    db.commit()
    db.refresh(mr)

    try:
        from app.tasks.integrations import sync_material_request_to_erp

        sync_material_request_to_erp.delay(str(mr.id))
    except Exception as exc:
        mr.erp_sync_status = MaterialRequestERPSyncStatus.failed
        mr.erp_sync_error = f"ERP sync enqueue failed: {exc}"[:500]
        db.commit()
        db.refresh(mr)
        logger.debug("ERP sync enqueue failed for material request.", exc_info=True)

    return mr


def _validate_items_exist_in_erp(db: Session, mr: MaterialRequest) -> None:
    """Validate that all material request item SKUs exist in ERP before approval."""
    from app.services.dotmac_erp import DotMacERPError
    from app.services.dotmac_erp.material_request_sync import dotmac_erp_material_request_sync

    try:
        sync_service = dotmac_erp_material_request_sync(db)
    except ValueError:
        # ERP not configured in this environment; keep existing behavior.
        return

    missing_codes: set[str] = set()
    checked: dict[str, bool] = {}
    try:
        for mr_item in mr.items:
            code = ((mr_item.item.sku if mr_item.item else None) or "").strip()
            if not code:
                missing_codes.add("(missing SKU)")
                continue
            if code not in checked:
                matches = sync_service.client.get_inventory_items(
                    limit=50,
                    offset=0,
                    search=code,
                    include_zero_stock=True,
                )
                checked[code] = any((entry.get("item_code") or "").strip() == code for entry in matches)
            if not checked[code]:
                missing_codes.add(code)
    except DotMacERPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Cannot validate ERP item codes right now: {exc}",
        ) from exc
    finally:
        sync_service.close()

    if missing_codes:
        codes = ", ".join(sorted(missing_codes))
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve material request. Item code(s) not found in ERP: {codes}",
        )


def normalize_serial_numbers(value: list[str] | str | None) -> list[str]:
    """Normalize user-entered serial numbers from form/API input."""
    if value is None:
        return []
    if isinstance(value, str):
        raw_values = value.replace(",", "\n").splitlines()
    else:
        raw_values = []
        for item in value:
            raw_values.extend(str(item).replace(",", "\n").splitlines())

    serials: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        serial = str(raw).strip()
        if not serial:
            continue
        key = serial.lower()
        if key in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate serial number selected: {serial}")
        seen.add(key)
        serials.append(serial)
    return serials


def _apply_serial_numbers(
    mr: MaterialRequest,
    serial_numbers_by_item: dict[str, list[str] | str] | None,
) -> None:
    if not serial_numbers_by_item:
        return
    item_by_id = {str(item.id): item for item in mr.items}
    for item_id, raw_serials in serial_numbers_by_item.items():
        mr_item = item_by_id.get(str(item_id))
        if not mr_item:
            raise HTTPException(status_code=400, detail="Serial numbers were submitted for an unknown request line")
        serials = normalize_serial_numbers(raw_serials)
        if serials and len(serials) != mr_item.quantity:
            item_name = mr_item.item.name if mr_item.item else "this item"
            raise HTTPException(
                status_code=400,
                detail=f"Select exactly {mr_item.quantity} serial number(s) for {item_name}",
            )
        mr_item.serial_numbers = serials or None


def _validate_serial_numbers_in_erp(db: Session, mr: MaterialRequest, source_location: InventoryLocation) -> None:
    """Require serial selections when ERP marks an item as serial-tracked."""
    from app.services.dotmac_erp import DotMacERPError
    from app.services.dotmac_erp.material_request_sync import dotmac_erp_material_request_sync

    warehouse_code = (source_location.code or str(source_location.id)).strip()
    try:
        sync_service = dotmac_erp_material_request_sync(db)
    except ValueError:
        return

    try:
        for mr_item in mr.items:
            item_code = ((mr_item.item.sku if mr_item.item else None) or "").strip()
            if not item_code:
                continue

            data = sync_service.client.list_available_serials(
                item_code=item_code,
                warehouse_code=warehouse_code,
                limit=500,
            )
            if not data.get("track_serial_numbers"):
                continue

            selected = normalize_serial_numbers(mr_item.serial_numbers)
            if len(selected) != mr_item.quantity:
                item_name = mr_item.item.name if mr_item.item else item_code
                raise HTTPException(
                    status_code=400,
                    detail=f"Select exactly {mr_item.quantity} serial number(s) for {item_name}",
                )

            if not data.get("has_more"):
                available_serials = {
                    str(serial.get("serial_number") or "").strip().lower()
                    for serial in data.get("serials", [])
                    if isinstance(serial, dict)
                }
                missing = [serial for serial in selected if serial.lower() not in available_serials]
                if missing:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Serial number(s) not available in ERP: {', '.join(missing)}",
                    )
    except DotMacERPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Cannot validate ERP serial numbers right now: {exc}",
        ) from exc
    finally:
        sync_service.close()


class MaterialRequests(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: MaterialRequestCreate) -> MaterialRequest:
        get_or_404(db, Person, str(payload.requested_by_person_id), detail="Person not found")

        if not payload.ticket_id and not payload.project_id:
            raise HTTPException(status_code=400, detail="Select either a linked ticket or a linked project")

        if payload.source_location_id:
            get_or_404(db, InventoryLocation, str(payload.source_location_id), detail="Source warehouse not found")
        if payload.destination_location_id:
            get_or_404(
                db,
                InventoryLocation,
                str(payload.destination_location_id),
                detail="Destination warehouse not found",
            )
        if payload.source_location_id and payload.destination_location_id == payload.source_location_id:
            raise HTTPException(status_code=400, detail="Source and destination warehouse cannot be the same")

        data = payload.model_dump(exclude={"items"})
        number = generate_number(
            db=db,
            domain=SettingDomain.numbering,
            sequence_key="material_request_number",
            enabled_key="material_request_number_enabled",
            prefix_key="material_request_number_prefix",
            padding_key="material_request_number_padding",
            start_key="material_request_number_start",
        )
        if number:
            data["number"] = number
        mr = MaterialRequest(
            **data,
            status=MaterialRequestStatus.submitted,
            submitted_at=datetime.now(UTC),
        )
        db.add(mr)
        db.flush()

        if payload.items:
            for item_payload in payload.items:
                get_or_404(db, InventoryItem, str(item_payload.item_id), detail="Inventory item not found")
                mr_item = MaterialRequestItem(
                    material_request_id=mr.id,
                    item_id=item_payload.item_id,
                    quantity=item_payload.quantity,
                    notes=item_payload.notes,
                    serial_numbers=normalize_serial_numbers(item_payload.serial_numbers) or None,
                )
                db.add(mr_item)

        db.commit()
        db.refresh(mr)
        return mr

    @staticmethod
    def get(db: Session, mr_id: str) -> MaterialRequest:
        return get_or_404(
            db,
            MaterialRequest,
            mr_id,
            options=[selectinload(MaterialRequest.items)],
        )

    @staticmethod
    def list(
        db: Session,
        is_active: bool | None = None,
        status: str | None = None,
        erp_status: str | None = None,
        ticket_id: str | None = None,
        project_id: str | None = None,
        priority: str | None = None,
        created_from: date | None = None,
        created_to: date | None = None,
        order_by: str = "created_at",
        order_dir: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> list[MaterialRequest]:
        query = db.query(MaterialRequest).options(selectinload(MaterialRequest.items))
        query = apply_is_active_filter(query, MaterialRequest, is_active)
        if status:
            validated_status = validate_enum(status, MaterialRequestStatus, "status")
            query = query.filter(MaterialRequest.status == validated_status)
        if erp_status:
            normalized_erp_status = erp_status.strip().lower().replace("-", "_").replace(" ", "_")
            if normalized_erp_status in {item.value for item in MaterialRequestERPSyncStatus}:
                query = query.filter(MaterialRequest.erp_sync_status == MaterialRequestERPSyncStatus(normalized_erp_status))
            else:
                query = query.filter(MaterialRequest.erp_material_status == normalized_erp_status)
        if ticket_id:
            query = query.filter(MaterialRequest.ticket_id == coerce_uuid(ticket_id))
        if project_id:
            query = query.filter(MaterialRequest.project_id == coerce_uuid(project_id))
        if priority:
            validated_priority = validate_enum(priority, MaterialRequestPriority, "priority")
            query = query.filter(MaterialRequest.priority == validated_priority)

        if created_from and created_to:
            if created_from > created_to:
                raise HTTPException(status_code=400, detail="From date must be before or equal to To date")
            range_start = datetime.combine(created_from, time.min, tzinfo=UTC)
            range_end_exclusive = datetime.combine(created_to + timedelta(days=1), time.min, tzinfo=UTC)
            query = query.filter(MaterialRequest.created_at >= range_start)
            query = query.filter(MaterialRequest.created_at < range_end_exclusive)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": MaterialRequest.created_at, "priority": MaterialRequest.priority},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, mr_id: str, payload: MaterialRequestUpdate) -> MaterialRequest:
        mr = get_or_404(db, MaterialRequest, mr_id, options=[selectinload(MaterialRequest.items)])
        if mr.status in _TERMINAL_STATUSES:
            raise HTTPException(status_code=400, detail=f"Cannot update material request in {mr.status.value} status")

        if payload.source_location_id:
            get_or_404(db, InventoryLocation, str(payload.source_location_id), detail="Source warehouse not found")
        if payload.destination_location_id:
            get_or_404(
                db,
                InventoryLocation,
                str(payload.destination_location_id),
                detail="Destination warehouse not found",
            )

        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(mr, field, value)
        db.commit()
        db.refresh(mr)
        return mr

    @staticmethod
    def submit(db: Session, mr_id: str) -> MaterialRequest:
        mr = get_or_404(db, MaterialRequest, mr_id, options=[selectinload(MaterialRequest.items)])
        if mr.status != MaterialRequestStatus.draft:
            raise HTTPException(status_code=400, detail="Only draft requests can be submitted")
        mr.status = MaterialRequestStatus.submitted
        mr.submitted_at = datetime.now(UTC)
        db.commit()
        db.refresh(mr)
        return mr

    @staticmethod
    def approve(
        db: Session,
        mr_id: str,
        approved_by_person_id: str,
        source_location_id: str | None = None,
        destination_location_id: str | None = None,
        collected_by_person_id: str | None = None,
        serial_numbers_by_item: dict[str, list[str] | str] | None = None,
    ) -> MaterialRequest:
        mr = get_or_404(
            db,
            MaterialRequest,
            mr_id,
            options=[selectinload(MaterialRequest.items).selectinload(MaterialRequestItem.item)],
        )
        if mr.status != MaterialRequestStatus.submitted:
            raise HTTPException(status_code=400, detail="Only submitted requests can be approved")

        approver_uuid = coerce_uuid(approved_by_person_id)
        get_or_404(db, Person, str(approver_uuid), detail="Approver not found")
        collected_by_uuid = coerce_uuid(collected_by_person_id) if collected_by_person_id else None
        if collected_by_uuid:
            get_or_404(db, Person, str(collected_by_uuid), detail="Collector not found")

        source_uuid = coerce_uuid(source_location_id) if source_location_id else mr.source_location_id
        destination_uuid = (
            coerce_uuid(destination_location_id) if destination_location_id else mr.destination_location_id
        )

        if not source_uuid:
            raise HTTPException(status_code=400, detail="Select a source warehouse before issuing this request")
        source_location = get_or_404(db, InventoryLocation, str(source_uuid), detail="Source warehouse not found")

        if destination_uuid:
            get_or_404(db, InventoryLocation, str(destination_uuid), detail="Destination warehouse not found")
            if destination_uuid == source_uuid:
                raise HTTPException(status_code=400, detail="Source and destination warehouse cannot be the same")

        _validate_items_exist_in_erp(db, mr)
        _apply_serial_numbers(mr, serial_numbers_by_item)
        _validate_serial_numbers_in_erp(db, mr, source_location)

        mr.source_location_id = source_uuid
        mr.destination_location_id = destination_uuid
        mr.status = MaterialRequestStatus.issued
        mr.erp_sync_status = MaterialRequestERPSyncStatus.pending
        mr.erp_sync_error = None
        mr.approved_by_person_id = approver_uuid
        mr.collected_by_person_id = collected_by_uuid
        mr.approved_at = datetime.now(UTC)
        db.commit()
        db.refresh(mr)

        return _enqueue_erp_sync(db, mr)

    @staticmethod
    def retry_erp_sync(db: Session, mr_id: str) -> MaterialRequest:
        mr = get_or_404(db, MaterialRequest, mr_id, options=[selectinload(MaterialRequest.items)])
        if mr.status not in (MaterialRequestStatus.approved, MaterialRequestStatus.issued):
            raise HTTPException(status_code=400, detail="Only issued material requests can be synced to ERP")
        return _enqueue_erp_sync(db, mr)

    @staticmethod
    def reject(db: Session, mr_id: str, approved_by_person_id: str, reason: str | None = None) -> MaterialRequest:
        mr = get_or_404(db, MaterialRequest, mr_id, options=[selectinload(MaterialRequest.items)])
        if mr.status != MaterialRequestStatus.submitted:
            raise HTTPException(status_code=400, detail="Only submitted requests can be rejected")
        get_or_404(db, Person, approved_by_person_id, detail="Reviewer not found")
        mr.status = MaterialRequestStatus.rejected
        mr.approved_by_person_id = coerce_uuid(approved_by_person_id)
        mr.rejected_at = datetime.now(UTC)
        if reason:
            mr.notes = (mr.notes or "") + f"\nRejection reason: {reason}"
        db.commit()
        db.refresh(mr)
        return mr

    @staticmethod
    def cancel(db: Session, mr_id: str) -> MaterialRequest:
        mr = get_or_404(db, MaterialRequest, mr_id, options=[selectinload(MaterialRequest.items)])
        if mr.status in _TERMINAL_STATUSES:
            raise HTTPException(status_code=400, detail=f"Cannot cancel material request in {mr.status.value} status")
        mr.status = MaterialRequestStatus.canceled
        db.commit()
        db.refresh(mr)
        return mr

    @staticmethod
    def add_item(db: Session, mr_id: str, payload: MaterialRequestItemCreate) -> MaterialRequestItem:
        mr = get_or_404(db, MaterialRequest, mr_id)
        if mr.status in _TERMINAL_STATUSES:
            raise HTTPException(status_code=400, detail="Cannot modify items on a finalized request")
        get_or_404(db, InventoryItem, str(payload.item_id), detail="Inventory item not found")
        item = MaterialRequestItem(
            material_request_id=mr.id,
            item_id=payload.item_id,
            quantity=payload.quantity,
            notes=payload.notes,
            serial_numbers=normalize_serial_numbers(payload.serial_numbers) or None,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    @staticmethod
    def remove_item(db: Session, mr_id: str, item_id: str) -> None:
        mr = get_or_404(db, MaterialRequest, mr_id)
        if mr.status in _TERMINAL_STATUSES:
            raise HTTPException(status_code=400, detail="Cannot modify items on a finalized request")
        mr_item = get_or_404(db, MaterialRequestItem, item_id, detail="Material request item not found")
        if mr_item.material_request_id != mr.id:
            raise HTTPException(status_code=404, detail="Material request item not found")
        db.delete(mr_item)
        db.commit()


material_requests = MaterialRequests()
