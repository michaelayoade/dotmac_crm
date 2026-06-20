"""Record installed equipment (ONT serials) from the field.

Closes the gap where OntAssignment tracked the installing technician but not
the customer: assignments created here link ONT → subscriber → work order, so
"what ONT does this customer have" is finally answerable.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.network import OntAssignment, OntUnit
from app.services.common import coerce_uuid
from app.services.field.jobs import get_scoped_work_order


class FieldEquipment:
    @staticmethod
    def record(
        db: Session,
        person_id: str,
        work_order_id: str,
        *,
        serial_number: str,
        vendor: str | None = None,
        model: str | None = None,
        notes: str | None = None,
    ) -> OntAssignment:
        work_order = get_scoped_work_order(db, person_id, work_order_id)
        if not work_order.subscriber_id:
            raise HTTPException(status_code=422, detail="Job has no subscriber to assign equipment to")
        serial = (serial_number or "").strip().upper()
        if not serial:
            raise HTTPException(status_code=422, detail="serial_number is required")

        unit = db.query(OntUnit).filter(OntUnit.serial_number == serial).first()
        if not unit:
            unit = OntUnit(serial_number=serial, vendor=vendor, model=model)
            db.add(unit)
            db.flush()
        elif vendor or model:
            unit.vendor = vendor or unit.vendor
            unit.model = model or unit.model

        now = datetime.now(UTC)
        # Replacement flow: a subscriber has at most one active ONT, and a unit
        # is active at one premises only. The unit side has a partial unique
        # index; the subscriber side is enforced here, so lock the prior active
        # rows FOR UPDATE to serialize concurrent records for the same
        # subscriber/unit (prevents two "active" assignments racing in).
        # Follow-up migration recommended: a partial unique index on
        # ont_assignments(subscriber_id) WHERE active, for DB-level enforcement.
        prior = (
            db.query(OntAssignment)
            .filter((OntAssignment.subscriber_id == work_order.subscriber_id) | (OntAssignment.ont_unit_id == unit.id))
            .filter(OntAssignment.active.is_(True))
            .with_for_update()
            .all()
        )
        for assignment in prior:
            assignment.active = False
        # Flush the deactivations before inserting the new active row so the
        # unit-side partial unique index sees a deterministic UPDATE→INSERT order.
        db.flush()

        assignment = OntAssignment(
            ont_unit_id=unit.id,
            subscriber_id=work_order.subscriber_id,
            work_order_id=work_order.id,
            person_id=coerce_uuid(person_id),
            assigned_at=now,
            active=True,
            notes=notes,
        )
        db.add(assignment)
        db.commit()
        db.refresh(assignment)
        return assignment

    @staticmethod
    def current_for_job(db: Session, person_id: str, work_order_id: str) -> OntAssignment | None:
        work_order = get_scoped_work_order(db, person_id, work_order_id)
        if not work_order.subscriber_id:
            return None
        return (
            db.query(OntAssignment)
            .filter(OntAssignment.subscriber_id == work_order.subscriber_id)
            .filter(OntAssignment.active.is_(True))
            .order_by(OntAssignment.assigned_at.desc().nullslast())
            .first()
        )


field_equipment = FieldEquipment()
