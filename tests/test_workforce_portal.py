"""Field-service tracker: work order → portal payload (technician/schedule/ETA)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.workforce import WorkOrderPriority, WorkOrderStatus, WorkOrderType
from app.services.workforce import build_work_order_portal_payload


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
