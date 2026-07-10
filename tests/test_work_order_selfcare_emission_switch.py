"""Tests for the work_order_events_to_selfcare_enabled kill switch (Phase 2 flip).

Default on preserves today's behavior (CRM pushes work-order lifecycle events to
the sub field-service mirror). Flipping it off is the flip-day lever that stops
emission cleanly — no errors, just a debug-log skip — without deleting call sites.
"""

from app.models.domain_settings import SettingDomain
from app.models.subscriber import Subscriber
from app.models.workforce import WorkOrderStatus, WorkOrderType
from app.schemas.workforce import WorkOrderCreate, WorkOrderUpdate
from app.services import settings_spec
from app.services import workforce as workforce_service


def _selfcare_work_order(db_session, person):
    subscriber = Subscriber(
        person_id=person.id,
        external_system="selfcare",
        external_id="sub-987",
        subscriber_number="SUB-987",
    )
    db_session.add(subscriber)
    db_session.flush()
    return workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(
            title="Install subscriber service",
            work_type=WorkOrderType.install,
            subscriber_id=subscriber.id,
        ),
    )


def _record_notify(monkeypatch):
    calls: list[tuple[str, str]] = []

    def _notify(_db, event_type, payload):
        calls.append((event_type, payload["subscriber_id"]))
        return True

    monkeypatch.setattr("app.services.selfcare.notify_work_order_event", _notify)
    return calls


def _disable_emission(monkeypatch):
    real_resolve = settings_spec.resolve_value

    def fake(db, domain, key, **kwargs):
        if (domain, key) == (SettingDomain.integration, "work_order_events_to_selfcare_enabled"):
            return False
        return real_resolve(db, domain, key, **kwargs)

    monkeypatch.setattr(settings_spec, "resolve_value", fake)


def test_emission_enabled_by_default(monkeypatch, db_session, person):
    work_order = _selfcare_work_order(db_session, person)
    calls = _record_notify(monkeypatch)

    notified = workforce_service._emit_work_order_to_sub(db_session, work_order, "work_order.updated")

    assert notified is True
    assert calls == [("work_order.updated", "sub-987")]


def test_emission_kill_switch_skips_selfcare(monkeypatch, db_session, person):
    work_order = _selfcare_work_order(db_session, person)
    calls = _record_notify(monkeypatch)
    _disable_emission(monkeypatch)

    notified = workforce_service._emit_work_order_to_sub(db_session, work_order, "work_order.updated")

    assert notified is False
    assert calls == []


def test_status_transition_respects_kill_switch(monkeypatch, db_session, person):
    work_order = _selfcare_work_order(db_session, person)
    calls = _record_notify(monkeypatch)
    _disable_emission(monkeypatch)

    updated = workforce_service.work_orders.update(
        db_session,
        str(work_order.id),
        WorkOrderUpdate(status=WorkOrderStatus.dispatched),
    )

    assert updated.status == WorkOrderStatus.dispatched
    assert calls == []
