"""Field-tech fiber-plant capture.

Technicians record connectivity they build in the field (splices), but the
fiber topology is a shared record of truth — a wrong strand pairing misroutes
real customers. So field input never mutates the plant directly: each capture
becomes a *pending* ``FiberChangeRequest`` that an engineer reviews and applies
through the existing change-request workflow. This module owns the field-side
validation (the invariants a tech could plausibly get wrong) and the proposal.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.fiber_change_request import (
    FiberChangeRequest,
    FiberChangeRequestOperation,
    FiberChangeRequestStatus,
)
from app.models.field import FiberTestResult, FieldAttachment
from app.models.network import (
    FdhCabinet,
    FiberAccessPoint,
    FiberSplice,
    FiberSpliceClosure,
    FiberSpliceTray,
    FiberStrand,
    FiberStrandStatus,
    OLTDevice,
)
from app.services import fiber_change_requests
from app.services.common import coerce_uuid
from app.services.field.jobs import get_scoped_work_order

# Assets a field optical test may target.
_TESTABLE_ASSET_MODELS = {
    "fiber_strand": FiberStrand,
    "fiber_splice": FiberSplice,
    "splice_closure": FiberSpliceClosure,
    "fiber_access_point": FiberAccessPoint,
    "fdh": FdhCabinet,
    "olt": OLTDevice,
}

# Recognised field test kinds (OTDR trace, power meter, loss/reflectance, etc.).
_TEST_TYPES = {"otdr", "optical_power", "insertion_loss", "reflectance", "continuity", "other"}

# A field splice may only consume strands that are free to use. in_use means
# the strand is already carrying service elsewhere; damaged/retired are unusable.
_SPLICEABLE_STRAND_STATUSES = {FiberStrandStatus.available, FiberStrandStatus.reserved}


def _load_spliceable_strand(db: Session, strand_id, label: str) -> FiberStrand:
    strand = db.get(FiberStrand, coerce_uuid(strand_id))
    if strand is None or not strand.is_active:
        raise HTTPException(status_code=404, detail=f"{label} strand not found")
    if strand.status not in _SPLICEABLE_STRAND_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"{label} strand is {strand.status.value}; only available or reserved strands can be spliced",
        )
    return strand


def propose_splice(
    db: Session,
    person_id: str | None,
    *,
    closure_id: str,
    from_strand_id: str,
    to_strand_id: str,
    tray_id: str | None = None,
    position: int | None = None,
    splice_type: str | None = None,
    loss_db: float | None = None,
    note: str | None = None,
) -> dict:
    """Validate a field-captured splice and file it for engineering review."""
    from_uuid = coerce_uuid(from_strand_id)
    to_uuid = coerce_uuid(to_strand_id)
    if from_uuid == to_uuid:
        raise HTTPException(status_code=422, detail="A strand cannot be spliced to itself")

    closure = db.get(FiberSpliceClosure, coerce_uuid(closure_id))
    if closure is None or not closure.is_active:
        raise HTTPException(status_code=404, detail="Splice closure not found")

    _load_spliceable_strand(db, from_uuid, "from")
    _load_spliceable_strand(db, to_uuid, "to")

    if tray_id is not None:
        tray = db.get(FiberSpliceTray, coerce_uuid(tray_id))
        if tray is None:
            raise HTTPException(status_code=404, detail="Splice tray not found")
        if tray.closure_id != closure.id:
            raise HTTPException(status_code=422, detail="Splice tray does not belong to this closure")
        if position is not None:
            occupied = (
                db.query(FiberSplice).filter(FiberSplice.tray_id == tray.id, FiberSplice.position == position).first()
            )
            if occupied:
                raise HTTPException(status_code=409, detail="That tray position is already occupied")

    # A splice between this strand pair (in either direction) already exists.
    existing = (
        db.query(FiberSplice)
        .filter(
            ((FiberSplice.from_strand_id == from_uuid) & (FiberSplice.to_strand_id == to_uuid))
            | ((FiberSplice.from_strand_id == to_uuid) & (FiberSplice.to_strand_id == from_uuid))
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="A splice between these strands already exists")

    # Idempotent double-submit: a still-pending proposal for the same pair is
    # returned as-is rather than queuing a second review.
    pair = {str(from_uuid), str(to_uuid)}
    for request in _pending_splice_requests(db):
        payload = request.payload or {}
        if {str(payload.get("from_strand_id")), str(payload.get("to_strand_id"))} == pair:
            return _proposal_response(request, replayed=True)

    splice_payload = {
        "closure_id": str(closure.id),
        "from_strand_id": str(from_uuid),
        "to_strand_id": str(to_uuid),
        "tray_id": str(coerce_uuid(tray_id)) if tray_id else None,
        "position": position,
        "splice_type": splice_type,
        "loss_db": loss_db,
        "notes": note,
    }
    request = fiber_change_requests.create_request(
        db,
        asset_type="fiber_splice",
        asset_id=None,
        operation=FiberChangeRequestOperation.create,
        payload=splice_payload,
        requested_by_person_id=str(person_id) if person_id else None,
        requested_by_vendor_id=None,
    )
    return _proposal_response(request, replayed=False)


def _pending_splice_requests(db: Session) -> list[FiberChangeRequest]:
    return (
        db.query(FiberChangeRequest)
        .filter(FiberChangeRequest.asset_type == "fiber_splice")
        .filter(FiberChangeRequest.status == FiberChangeRequestStatus.pending)
        .all()
    )


def _proposal_response(request: FiberChangeRequest, *, replayed: bool) -> dict:
    payload = request.payload or {}
    return {
        "change_request_id": request.id,
        "status": request.status.value,
        "replayed": replayed,
        "closure_id": payload.get("closure_id"),
        "from_strand_id": payload.get("from_strand_id"),
        "to_strand_id": payload.get("to_strand_id"),
    }


def record_test(
    db: Session,
    person_id: str | None,
    *,
    work_order_id: str,
    asset_type: str,
    asset_id: str,
    test_type: str,
    wavelength_nm: int | None = None,
    value_db: float | None = None,
    unit: str | None = None,
    passed: bool | None = None,
    instrument: str | None = None,
    measured_at: datetime | None = None,
    notes: str | None = None,
    attachment_id: str | None = None,
    client_ref: str | None = None,
) -> FiberTestResult:
    """Record a field optical test reading against a network asset."""
    if not person_id:
        raise HTTPException(status_code=404, detail="Job not found")
    # Governed by the work order the tech is on (404 if it isn't theirs).
    work_order = get_scoped_work_order(db, person_id, work_order_id)

    if test_type not in _TEST_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown test_type '{test_type}'. Allowed: {', '.join(sorted(_TEST_TYPES))}",
        )
    model = _TESTABLE_ASSET_MODELS.get(asset_type)
    if model is None:
        raise HTTPException(status_code=400, detail=f"Unsupported asset type: {asset_type}")
    asset_uuid = coerce_uuid(asset_id)
    if not db.get(model, asset_uuid):
        raise HTTPException(status_code=404, detail="Asset not found")

    # Offline idempotency: a retried upload returns the original row.
    client_ref_uuid = coerce_uuid(client_ref) if client_ref else None
    if client_ref_uuid:
        existing = db.query(FiberTestResult).filter(FiberTestResult.client_ref == client_ref_uuid).first()
        if existing:
            return existing

    attachment_uuid = coerce_uuid(attachment_id) if attachment_id else None
    if attachment_uuid:
        attachment = db.get(FieldAttachment, attachment_uuid)
        if not attachment or not attachment.is_active:
            raise HTTPException(status_code=404, detail="Attachment not found")

    result = FiberTestResult(
        work_order_id=work_order.id,
        asset_type=asset_type,
        asset_id=asset_uuid,
        test_type=test_type,
        wavelength_nm=wavelength_nm,
        value_db=value_db,
        unit=unit,
        passed=passed,
        instrument=instrument,
        attachment_id=attachment_uuid,
        measured_by_person_id=coerce_uuid(person_id) if person_id else None,
        measured_at=measured_at,
        notes=notes,
        client_ref=client_ref_uuid,
    )
    db.add(result)
    try:
        db.commit()
    except IntegrityError:
        # Concurrent retry raced us on client_ref: serve the winner's row.
        db.rollback()
        existing = db.query(FiberTestResult).filter(FiberTestResult.client_ref == client_ref_uuid).first()
        if existing:
            return existing
        raise
    db.refresh(result)
    return result


def list_tests(db: Session, person_id: str | None, *, work_order_id: str) -> list[FiberTestResult]:
    """Test readings captured on a work order the caller is assigned to."""
    if not person_id:
        raise HTTPException(status_code=404, detail="Job not found")
    work_order = get_scoped_work_order(db, person_id, work_order_id)
    return (
        db.query(FiberTestResult)
        .filter(FiberTestResult.work_order_id == work_order.id)
        .order_by(FiberTestResult.created_at.desc())
        .all()
    )
