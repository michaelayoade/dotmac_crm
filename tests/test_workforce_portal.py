"""Field-service tracker: work order → portal payload (technician/schedule/ETA)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.models.workforce import (
    WorkOrder,
    WorkOrderPriority,
    WorkOrderStatus,
    WorkOrderType,
)
from app.services.workforce import WorkOrders, build_work_order_portal_payload

_WO_ID = "11111111-1111-1111-1111-111111111111"
_SUB_ID = "22222222-2222-2222-2222-222222222222"


def _work_order(**kw):
    return SimpleNamespace(
        id="wo1",
        title=kw.get("title", "Fault repair"),
        status=kw.get("status", WorkOrderStatus.dispatched),
        work_type=kw.get("work_type", WorkOrderType.repair),
        priority=kw.get("priority", WorkOrderPriority.high),
        assigned_to_person_id=kw.get("assigned_to_person_id", "person-1"),
        subscriber=None,
        subscriber_id=None,
        scheduled_start=kw.get("scheduled_start", datetime(2026, 6, 30, 9, tzinfo=UTC)),
        scheduled_end=None,
        estimated_arrival_at=kw.get("estimated_arrival_at", datetime(2026, 6, 30, 9, 30, tzinfo=UTC)),
        estimated_duration_minutes=60,
        completed_at=None,
        created_at=datetime(2026, 6, 29, tzinfo=UTC),
    )


def test_payload_resolves_technician_and_schedule():
    person = SimpleNamespace(first_name="Ade", last_name="Tech", display_name=None, phone="+2348000000000")
    db = MagicMock()
    db.get.return_value = person

    out = build_work_order_portal_payload(db, _work_order())
    assert out["id"] == "wo1"
    assert out["status"] == "dispatched"
    assert out["work_type"] == "repair"
    assert out["priority"] == "high"
    assert out["technician_name"] == "Ade Tech"
    assert out["technician_phone"] == "+2348000000000"
    assert out["estimated_duration_minutes"] == 60
    assert out["scheduled_start"].startswith("2026-06-30T09:00")
    assert out["estimated_arrival_at"].startswith("2026-06-30T09:30")


def test_payload_handles_no_technician():
    db = MagicMock()
    out = build_work_order_portal_payload(db, _work_order(assigned_to_person_id=None))
    assert out["technician_name"] is None
    assert out["technician_phone"] is None
    db.get.assert_not_called()


# --- technician live location (Start work → End work window) -----------------


def _presence(**kw):
    return SimpleNamespace(
        location_sharing_enabled=kw.get("sharing", True),
        last_latitude=kw.get("lat", 6.5),
        last_longitude=kw.get("lng", 3.3),
        last_location_accuracy_m=kw.get("acc", 12.0),
        # Default to a fresh fix so the freshness gate passes; override with a
        # stale `at=` to exercise the staleness path.
        last_location_at=kw.get("at", datetime.now(UTC)),
    )


def _loc_wo(**kw):
    return SimpleNamespace(
        id=_WO_ID,
        status=kw.get("status", WorkOrderStatus.in_progress),
        assigned_to_person_id=kw.get("assigned_to_person_id", "person-1"),
        estimated_arrival_at=datetime(2026, 6, 30, 9, 30, tzinfo=UTC),
    )


def _db_for(work_order, presence):
    """MagicMock db whose .query(Model).filter(...).first() yields the right row."""
    db = MagicMock()

    def _query(model):
        chain = MagicMock()
        chain.filter.return_value = chain
        chain.first.return_value = work_order if model is WorkOrder else presence
        return chain

    db.query.side_effect = _query
    return db


def test_technician_location_available_when_in_progress_and_sharing():
    db = _db_for(_loc_wo(), _presence())
    out = WorkOrders.portal_technician_location(db, _WO_ID, _SUB_ID)
    assert out["available"] is True
    assert out["latitude"] == 6.5
    assert out["longitude"] == 3.3
    assert out["updated_at"]  # fresh isoformat timestamp present
    assert out["estimated_arrival_at"].startswith("2026-06-30T09:30")


def test_technician_location_hidden_when_stale():
    # A fix older than the freshness window must not be presented as live.
    stale = datetime(2026, 6, 30, 9, 15, tzinfo=UTC)
    db = _db_for(_loc_wo(), _presence(at=stale))
    out = WorkOrders.portal_technician_location(db, _WO_ID, _SUB_ID)
    assert out == {"available": False, "reason": "no_fix"}


def test_technician_location_hidden_before_start_work():
    db = _db_for(_loc_wo(status=WorkOrderStatus.dispatched), _presence())
    out = WorkOrders.portal_technician_location(db, _WO_ID, _SUB_ID)
    assert out == {"available": False, "reason": "not_in_progress"}


def test_technician_location_hidden_when_sharing_off():
    db = _db_for(_loc_wo(), _presence(sharing=False))
    out = WorkOrders.portal_technician_location(db, _WO_ID, _SUB_ID)
    assert out == {"available": False, "reason": "sharing_off"}


def test_technician_location_not_found_for_wrong_subscriber():
    db = _db_for(None, _presence())
    out = WorkOrders.portal_technician_location(db, _WO_ID, _SUB_ID)
    assert out == {"available": False, "reason": "not_found"}


# --- technician rating (reuses CSAT/Survey) ---------------------------------


def _db_rating(work_order, existing_response):
    """MagicMock db routing WorkOrder / Survey / SurveyResponse queries.

    The rating flow resolves the survey first (get-or-create) and then scopes the
    dedup to that survey_id, so the mock must return a survey with an ``id``.
    """
    from app.models.comms import Survey

    db = MagicMock()
    survey = SimpleNamespace(id="survey-1")

    def _query(model):
        chain = MagicMock()
        chain.filter.return_value = chain
        if model is WorkOrder:
            chain.first.return_value = work_order
        elif model is Survey:
            chain.first.return_value = survey
        else:  # SurveyResponse
            chain.first.return_value = existing_response
        return chain

    db.query.side_effect = _query
    return db


def test_rating_not_found_for_wrong_subscriber():
    db = _db_rating(None, None)
    with pytest.raises(HTTPException) as exc:
        WorkOrders.submit_technician_rating(db, _WO_ID, _SUB_ID, rating=5)
    assert exc.value.status_code == 404


def test_rating_rejected_when_not_completed():
    wo = _loc_wo(status=WorkOrderStatus.in_progress)
    db = _db_rating(wo, None)
    with pytest.raises(HTTPException) as exc:
        WorkOrders.submit_technician_rating(db, _WO_ID, _SUB_ID, rating=5)
    assert exc.value.status_code == 409


def test_rating_returns_existing_when_already_rated():
    wo = _loc_wo(status=WorkOrderStatus.completed)
    prior = SimpleNamespace(rating=4)
    db = _db_rating(wo, prior)
    out = WorkOrders.submit_technician_rating(db, _WO_ID, _SUB_ID, rating=5)
    assert out == {
        "ok": True,
        "already_rated": True,
        "rating": 4,
        "work_order_id": _WO_ID,
    }
