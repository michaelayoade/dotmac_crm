"""Tests for the customer "Track My Visit" feature: tokens, timeline, the live
technician-position privacy gate, and the routed confirm/reschedule actions."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from starlette.requests import Request

from app.models.field import FieldJobEvent, WorkOrderEvent
from app.models.field_location import FieldPresenceStatus, FieldTechPresence
from app.models.person import Person
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.schemas.tickets import TicketCreate
from app.services.field import tracking
from app.services.tickets import tickets as tickets_service

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_work_order(db, *, status=WorkOrderStatus.scheduled, assigned_to=None, **kwargs):
    wo = WorkOrder(title="Install ONT", status=status, assigned_to_person_id=assigned_to, **kwargs)
    db.add(wo)
    db.commit()
    db.refresh(wo)
    return wo


def _add_event(db, wo, event, *, when=None):
    db.add(
        WorkOrderEvent(
            work_order_id=wo.id,
            event=event,
            occurred_at=when or datetime.now(UTC),
            client_event_id=uuid.uuid4(),
        )
    )
    db.commit()


def _make_tech(db):
    person = Person(first_name="Alex", last_name="Rivera", email=f"tech-{uuid.uuid4().hex[:8]}@example.com")
    db.add(person)
    db.commit()
    db.refresh(person)
    return person


def _set_presence(db, person, *, sharing=True, age_seconds=10, lat=6.5, lng=3.3):
    db.add(
        FieldTechPresence(
            person_id=person.id,
            status=FieldPresenceStatus.on_shift,
            location_sharing_enabled=sharing,
            last_latitude=lat,
            last_longitude=lng,
            last_location_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
        )
    )
    db.commit()


# ── tokens ───────────────────────────────────────────────────────────────────


def test_token_get_or_create_is_idempotent(db_session):
    wo = _make_work_order(db_session)
    a = tracking.tokens.get_or_create(db_session, wo)
    b = tracking.tokens.get_or_create(db_session, wo)
    assert a.id == b.id
    assert a.token and len(a.token) >= 32


def test_token_state_classification(db_session):
    wo = _make_work_order(db_session)
    token_row = tracking.tokens.get_or_create(db_session, wo)
    assert tracking.token_state(token_row) == "ok"
    assert tracking.token_state(None) == "not_found"

    token_row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    assert tracking.token_state(token_row) == "expired"

    token_row.expires_at = datetime.now(UTC) + timedelta(days=1)
    wo.status = WorkOrderStatus.completed
    wo.completed_at = datetime.now(UTC) - timedelta(days=8)
    db_session.commit()
    assert tracking.token_state(token_row) == "closed"


# ── timeline ─────────────────────────────────────────────────────────────────


def test_timeline_progresses_with_events(db_session):
    tech = _make_tech(db_session)
    wo = _make_work_order(db_session, status=WorkOrderStatus.dispatched, assigned_to=tech.id)
    _add_event(db_session, wo, FieldJobEvent.en_route)

    steps = {s["key"]: s for s in tracking.build_timeline(db_session, wo)}
    assert steps["booked"]["state"] == "done"
    assert steps["assigned"]["state"] == "done"
    assert steps["en_route"]["state"] == "done"
    assert steps["started"]["state"] == "upcoming"  # status dispatched, not yet arrived
    assert "completed" in steps


def test_timeline_marks_missed_visit_as_failed(db_session):
    wo = _make_work_order(db_session, status=WorkOrderStatus.canceled)
    _add_event(db_session, wo, FieldJobEvent.unable_to_complete)
    last = tracking.build_timeline(db_session, wo)[-1]
    assert last["key"] == "missed"
    assert last["state"] == "failed"


# ── live-position privacy gate ───────────────────────────────────────────────


def _ready_live_setup(db, **presence_kwargs):
    tech = _make_tech(db)
    wo = _make_work_order(db, status=WorkOrderStatus.dispatched, assigned_to=tech.id)
    _add_event(db, wo, FieldJobEvent.en_route)
    _set_presence(db, tech, **presence_kwargs)
    return wo


def test_live_position_visible_when_on_the_way(db_session):
    wo = _ready_live_setup(db_session)
    pos = tracking.public_live_position(db_session, wo)
    assert pos is not None
    assert pos["latitude"] == 6.5 and pos["longitude"] == 3.3
    assert pos["name"] == "Alex"  # first name only — never last name


def test_live_position_hidden_without_sharing(db_session):
    wo = _ready_live_setup(db_session, sharing=False)
    assert tracking.public_live_position(db_session, wo) is None


def test_live_position_hidden_when_stale(db_session):
    wo = _ready_live_setup(db_session, age_seconds=600)
    assert tracking.public_live_position(db_session, wo) is None


def test_live_position_hidden_before_en_route(db_session):
    tech = _make_tech(db_session)
    wo = _make_work_order(db_session, status=WorkOrderStatus.dispatched, assigned_to=tech.id)
    _set_presence(db_session, tech)  # sharing + fresh, but no en_route event yet
    assert tracking.public_live_position(db_session, wo) is None


def test_live_position_hidden_when_not_dispatched(db_session):
    tech = _make_tech(db_session)
    wo = _make_work_order(db_session, status=WorkOrderStatus.in_progress, assigned_to=tech.id)
    _add_event(db_session, wo, FieldJobEvent.en_route)
    _set_presence(db_session, tech)
    assert tracking.public_live_position(db_session, wo) is None


# ── routed actions ───────────────────────────────────────────────────────────


def test_confirm_appointment_is_idempotent(db_session):
    wo = _make_work_order(db_session)
    token_row = tracking.tokens.get_or_create(db_session, wo)
    first = tracking.confirm_appointment(db_session, token_row)
    assert first == {"confirmed": True, "already": False}
    second = tracking.confirm_appointment(db_session, token_row)
    assert second["already"] is True
    db_session.refresh(wo)
    assert wo.metadata_.get("customer_confirmed_at")


def test_reschedule_request_single_open_guard(db_session):
    wo = _make_work_order(db_session)
    token_row = tracking.tokens.get_or_create(db_session, wo)
    tracking.request_reschedule(db_session, token_row, preferred_window="Thursday PM")
    db_session.refresh(wo)
    assert wo.metadata_["reschedule_request"]["status"] == "open"
    with pytest.raises(HTTPException) as exc:
        tracking.request_reschedule(db_session, token_row, note="again")
    assert exc.value.status_code == 409


# ── entry point: field_visit ticket → dispatch queue ─────────────────────────


def test_field_visit_ticket_enqueues_work_order(db_session):
    from app.models.dispatch import DispatchQueueStatus, WorkOrderAssignmentQueue
    from app.models.work_lifecycle import WorkEntityType, WorkLink, WorkLinkRelationship

    ticket = tickets_service.create(db_session, TicketCreate(title="No signal", tags=["field_visit"]))
    wo = db_session.query(WorkOrder).filter(WorkOrder.ticket_id == ticket.id).first()
    assert wo is not None
    queue_row = (
        db_session.query(WorkOrderAssignmentQueue).filter(WorkOrderAssignmentQueue.work_order_id == wo.id).first()
    )
    assert queue_row is not None
    assert queue_row.status == DispatchQueueStatus.queued
    link = (
        db_session.query(WorkLink)
        .filter(WorkLink.source_type == WorkEntityType.ticket)
        .filter(WorkLink.source_id == ticket.id)
        .filter(WorkLink.target_type == WorkEntityType.work_order)
        .filter(WorkLink.target_id == wo.id)
        .filter(WorkLink.relationship == WorkLinkRelationship.originated)
        .one_or_none()
    )
    assert link is not None
    assert link.contract_name == "ticket.field_visit.created_work_order"


# ── public route smoke ───────────────────────────────────────────────────────


def test_route_page_and_live_and_404(db_session):
    from app.web.public import track as track_web

    wo = _make_work_order(db_session, status=WorkOrderStatus.scheduled)
    token_row = tracking.tokens.get_or_create(db_session, wo)

    assert tracking.token_state(token_row) == "ok"

    live = track_web.track_live(token_row.token, db_session)
    assert live.status_code == 200
    body = json.loads(live.body)
    assert body["available"] is True
    assert "timeline" in body and "destination" in body

    missing = track_web.track_live("not-a-real-token", db_session)
    assert missing.status_code == 404


def test_route_serializes_live_tech_pin(db_session):
    """Regression: the live pin carries a datetime — the page (tojson) and the
    /live JSONResponse must serialize it without a 500."""
    from app.web.public import track as track_web

    tech = _make_tech(db_session)
    wo = _make_work_order(db_session, status=WorkOrderStatus.dispatched, assigned_to=tech.id)
    _add_event(db_session, wo, FieldJobEvent.en_route)
    _set_presence(db_session, tech)
    token_row = tracking.tokens.get_or_create(db_session, wo)

    encoded_state = jsonable_encoder(tracking.public_state(db_session, token_row.work_order))
    assert isinstance(encoded_state["tech_position"]["updated_at"], str)

    live = track_web.track_live(token_row.token, db_session)
    assert live.status_code == 200
    pos = json.loads(live.body)["tech_position"]
    assert pos is not None
    assert pos["latitude"] == 6.5 and pos["longitude"] == 3.3
    assert isinstance(pos["updated_at"], str)  # ISO string, JSON-safe


# ── follow-ups: terminal-state action gating (#2) ────────────────────────────


def test_confirm_rejected_on_completed(db_session):
    wo = _make_work_order(db_session, status=WorkOrderStatus.completed)
    token_row = tracking.tokens.get_or_create(db_session, wo)
    with pytest.raises(HTTPException) as exc:
        tracking.confirm_appointment(db_session, token_row)
    assert exc.value.status_code == 409


def test_reschedule_rejected_on_completed_but_allowed_when_missed(db_session):
    done = _make_work_order(db_session, status=WorkOrderStatus.completed)
    with pytest.raises(HTTPException) as exc:
        tracking.request_reschedule(db_session, tracking.tokens.get_or_create(db_session, done))
    assert exc.value.status_code == 409

    # A canceled ("missed") visit must still accept a reschedule request — that
    # is exactly what the missed-visit message invites.
    missed = _make_work_order(db_session, status=WorkOrderStatus.canceled)
    res = tracking.request_reschedule(
        db_session, tracking.tokens.get_or_create(db_session, missed), preferred_window="tomorrow"
    )
    assert res == {"requested": True}


# ── follow-ups: completion-email summary (#4) ────────────────────────────────


def test_completion_summary_excludes_internal_and_never_list_repr(db_session):
    from app.models.workforce import WorkOrderNote
    from app.services.eta_notifications import _completion_summary

    wo = _make_work_order(db_session)
    db_session.add(WorkOrderNote(work_order_id=wo.id, body="Customer requested reschedule", is_internal=True))
    db_session.add(WorkOrderNote(work_order_id=wo.id, body="Replaced the ONT; signal good.", is_internal=False))
    db_session.commit()
    db_session.refresh(wo)

    summary = _completion_summary(wo)
    assert summary == "Replaced the ONT; signal good."
    assert "WorkOrderNote" not in summary  # never the Python list repr
    assert "reschedule" not in summary.lower()  # internal note never leaks


def test_completion_summary_default_when_no_customer_notes(db_session):
    from app.services.eta_notifications import _completion_summary

    wo = _make_work_order(db_session)
    assert _completion_summary(wo) == "Work completed successfully."


# ── follow-ups: auto-assign notifies the customer (#3) ───────────────────────


def test_auto_assign_notifies_customer_with_link(db_session, monkeypatch):
    from app.models.dispatch import TechnicianProfile
    from app.services import dispatch as dispatch_service
    from app.services import eta_notifications

    tech = _make_tech(db_session)
    db_session.add(TechnicianProfile(person_id=tech.id, is_active=True))
    db_session.commit()
    wo = _make_work_order(db_session, status=WorkOrderStatus.draft)

    calls: list[str] = []
    monkeypatch.setattr(eta_notifications, "send_technician_assigned_notification", lambda db, wid: calls.append(wid))
    dispatch_service.auto_assign_work_order(db_session, str(wo.id))

    db_session.refresh(wo)
    assert wo.assigned_to_person_id == tech.id
    assert calls == [str(wo.id)]


# ── follow-ups: rate limiting (#1) ───────────────────────────────────────────


def test_rate_limit_blocks_after_threshold():
    from app.web.public import track as track_web

    dep = track_web._rate_limit("action", 10, 300)

    def request() -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/track/token/confirm",
                "headers": [(b"x-forwarded-for", b"203.0.113.77")],
                "client": ("127.0.0.1", 12345),
            }
        )

    for _ in range(10):
        dep(request())
    with pytest.raises(HTTPException) as exc:
        dep(request())
    assert exc.value.status_code == 429
