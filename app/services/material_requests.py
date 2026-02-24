import logging
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload

from app.models.domain_settings import SettingDomain
from app.models.inventory import InventoryItem
from app.models.material_request import (
    MaterialRequest,
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
_TERMINAL_STATUSES = {
    MaterialRequestStatus.issued,
    MaterialRequestStatus.approved,
    MaterialRequestStatus.fulfilled,
    MaterialRequestStatus.rejected,
    MaterialRequestStatus.canceled,
}


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


class MaterialRequests(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: MaterialRequestCreate) -> MaterialRequest:
        get_or_404(db, Person, str(payload.requested_by_person_id), detail="Person not found")
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
        ticket_id: str | None = None,
        project_id: str | None = None,
        priority: str | None = None,
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
        if ticket_id:
            query = query.filter(MaterialRequest.ticket_id == coerce_uuid(ticket_id))
        if project_id:
            query = query.filter(MaterialRequest.project_id == coerce_uuid(project_id))
        if priority:
            validated_priority = validate_enum(priority, MaterialRequestPriority, "priority")
            query = query.filter(MaterialRequest.priority == validated_priority)
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
    def approve(db: Session, mr_id: str, approved_by_person_id: str) -> MaterialRequest:
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
        _validate_items_exist_in_erp(db, mr)

        mr.status = MaterialRequestStatus.issued
        mr.approved_by_person_id = approver_uuid
        mr.approved_at = datetime.now(UTC)
        db.commit()
        db.refresh(mr)

        # Trigger ERP sync task
        try:
            from app.tasks.integrations import sync_material_request_to_erp

            sync_material_request_to_erp.delay(str(mr.id))
        except Exception:
            logger.debug("ERP sync enqueue failed for material request approval.", exc_info=True)

        return mr

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
