import pytest
from fastapi import HTTPException

from app.models.fiber_change_request import FiberChangeRequest, FiberChangeRequestStatus
from app.models.network import (
    FiberSplice,
    FiberSpliceClosure,
    FiberSpliceTray,
    FiberStrand,
    FiberStrandStatus,
)
from app.services import fiber_change_requests
from app.services.field.fiber import propose_splice


def _closure_with_strands(
    db_session, *, from_status=FiberStrandStatus.available, to_status=FiberStrandStatus.available
):
    closure = FiberSpliceClosure(name="Closure A")
    a = FiberStrand(cable_name="CBL-1", strand_number=1, status=from_status)
    b = FiberStrand(cable_name="CBL-1", strand_number=2, status=to_status)
    db_session.add_all([closure, a, b])
    db_session.commit()
    return closure, a, b


def test_propose_splice_files_pending_change_request(db_session, person):
    closure, a, b = _closure_with_strands(db_session)

    result = propose_splice(
        db_session,
        str(person.id),
        closure_id=str(closure.id),
        from_strand_id=str(a.id),
        to_strand_id=str(b.id),
        splice_type="fusion",
        loss_db=0.08,
    )

    assert result["replayed"] is False
    assert result["status"] == "pending"
    request = db_session.get(FiberChangeRequest, result["change_request_id"])
    assert request.asset_type == "fiber_splice"
    assert request.status == FiberChangeRequestStatus.pending
    assert request.requested_by_person_id == person.id
    assert request.payload["from_strand_id"] == str(a.id)
    assert request.payload["loss_db"] == 0.08
    # Nothing is written to the plant until review.
    assert db_session.query(FiberSplice).count() == 0


def test_proposed_splice_applies_on_approval(db_session, person):
    closure, a, b = _closure_with_strands(db_session)
    result = propose_splice(
        db_session,
        str(person.id),
        closure_id=str(closure.id),
        from_strand_id=str(a.id),
        to_strand_id=str(b.id),
    )

    fiber_change_requests.approve_request(db_session, str(result["change_request_id"]), str(person.id), "ok")

    splice = db_session.query(FiberSplice).one()
    assert splice.closure_id == closure.id
    assert splice.from_strand_id == a.id
    assert splice.to_strand_id == b.id


def test_propose_splice_rejects_self_splice(db_session, person):
    closure, a, _ = _closure_with_strands(db_session)

    with pytest.raises(HTTPException) as exc:
        propose_splice(
            db_session,
            str(person.id),
            closure_id=str(closure.id),
            from_strand_id=str(a.id),
            to_strand_id=str(a.id),
        )

    assert exc.value.status_code == 422


def test_propose_splice_rejects_in_use_strand(db_session, person):
    closure, a, b = _closure_with_strands(db_session, to_status=FiberStrandStatus.in_use)

    with pytest.raises(HTTPException) as exc:
        propose_splice(
            db_session,
            str(person.id),
            closure_id=str(closure.id),
            from_strand_id=str(a.id),
            to_strand_id=str(b.id),
        )

    assert exc.value.status_code == 422
    assert "in_use" in exc.value.detail


def test_propose_splice_rejects_tray_from_other_closure(db_session, person):
    closure, a, b = _closure_with_strands(db_session)
    other = FiberSpliceClosure(name="Closure B")
    db_session.add(other)
    db_session.commit()
    tray = FiberSpliceTray(closure_id=other.id, tray_number=1)
    db_session.add(tray)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        propose_splice(
            db_session,
            str(person.id),
            closure_id=str(closure.id),
            from_strand_id=str(a.id),
            to_strand_id=str(b.id),
            tray_id=str(tray.id),
        )

    assert exc.value.status_code == 422


def test_propose_splice_rejects_existing_pair(db_session, person):
    closure, a, b = _closure_with_strands(db_session)
    db_session.add(FiberSplice(closure_id=closure.id, from_strand_id=b.id, to_strand_id=a.id))
    db_session.commit()

    # Same pair, reversed direction — still a duplicate physical splice.
    with pytest.raises(HTTPException) as exc:
        propose_splice(
            db_session,
            str(person.id),
            closure_id=str(closure.id),
            from_strand_id=str(a.id),
            to_strand_id=str(b.id),
        )

    assert exc.value.status_code == 409


def test_propose_splice_is_idempotent_for_pending_pair(db_session, person):
    closure, a, b = _closure_with_strands(db_session)
    first = propose_splice(
        db_session,
        str(person.id),
        closure_id=str(closure.id),
        from_strand_id=str(a.id),
        to_strand_id=str(b.id),
    )

    second = propose_splice(
        db_session,
        str(person.id),
        closure_id=str(closure.id),
        from_strand_id=str(a.id),
        to_strand_id=str(b.id),
    )

    assert second["replayed"] is True
    assert second["change_request_id"] == first["change_request_id"]
    assert db_session.query(FiberChangeRequest).count() == 1
