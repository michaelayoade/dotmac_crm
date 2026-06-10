"""Field material consumption — closes the loop the warehouse flow leaves open.

Reuses existing engines: ``inventory.consume_reservation`` for stock decrement
and ``material_requests.fulfill`` for the request lifecycle. This layer adds
caller scoping, per-item consumed quantities, and the "all used → request
fulfilled" rollup.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload

from app.models.inventory import MaterialStatus, WorkOrderMaterial
from app.models.material_request import MaterialRequest, MaterialRequestStatus
from app.services import inventory as inventory_service
from app.services.common import coerce_uuid
from app.services.field.jobs import get_scoped_work_order
from app.services.material_requests import material_requests


class FieldMaterials:
    @staticmethod
    def list_for_job(db: Session, person_id: str, work_order_id: str) -> list[WorkOrderMaterial]:
        work_order = get_scoped_work_order(db, person_id, work_order_id)
        return (
            db.query(WorkOrderMaterial)
            .options(selectinload(WorkOrderMaterial.item))
            .filter(WorkOrderMaterial.work_order_id == work_order.id)
            .order_by(WorkOrderMaterial.created_at.asc())
            .all()
        )

    @staticmethod
    def consume(
        db: Session,
        person_id: str,
        work_order_id: str,
        items: list[dict],
    ) -> list[WorkOrderMaterial]:
        """Record consumed quantities for job materials.

        Fully consumed materials flip to ``used`` and consume their inventory
        reservation (decrementing stock). When every material on the job is
        used, linked issued material requests are marked fulfilled.
        """
        work_order = get_scoped_work_order(db, person_id, work_order_id)
        if not items:
            raise HTTPException(status_code=422, detail="No materials to consume")

        updated: list[WorkOrderMaterial] = []
        for entry in items:
            material = db.get(WorkOrderMaterial, coerce_uuid(str(entry.get("material_id"))))
            if not material or material.work_order_id != work_order.id:
                raise HTTPException(status_code=404, detail="Material not found on this job")
            consumed = int(entry.get("consumed_quantity", 0))
            if consumed < 0:
                raise HTTPException(status_code=422, detail="consumed_quantity cannot be negative")
            if consumed > material.quantity:
                raise HTTPException(
                    status_code=422,
                    detail=f"Cannot consume {consumed} of {material.quantity} allocated",
                )
            material.consumed_quantity = consumed
            leftover_note = entry.get("leftover_note")
            if leftover_note:
                material.notes = ((material.notes or "") + f"\nLeftover: {leftover_note}").strip()
            if consumed == material.quantity:
                material.status = MaterialStatus.used
                if material.reservation_id:
                    inventory_service.consume_reservation(db, str(material.reservation_id))
            updated.append(material)

        db.commit()
        for material in updated:
            db.refresh(material)

        FieldMaterials._fulfill_requests_if_done(db, work_order.id)
        return updated

    @staticmethod
    def _fulfill_requests_if_done(db: Session, work_order_id) -> None:
        remaining = (
            db.query(WorkOrderMaterial)
            .filter(WorkOrderMaterial.work_order_id == work_order_id)
            .filter(WorkOrderMaterial.status != MaterialStatus.used)
            .count()
        )
        if remaining:
            return
        issued_requests = (
            db.query(MaterialRequest)
            .filter(MaterialRequest.work_order_id == work_order_id)
            .filter(MaterialRequest.status == MaterialRequestStatus.issued)
            .all()
        )
        for request in issued_requests:
            material_requests.fulfill(db, str(request.id))


field_materials = FieldMaterials()
