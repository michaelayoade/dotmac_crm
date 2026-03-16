from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.notification import Notification, NotificationChannel
from app.models.person import Person
from app.models.subscriber import Subscriber
from app.schemas.workforce import WorkOrderCreate, WorkOrderUpdate
from app.services import workforce as workforce_service


def test_work_order_create_notifies_assigned_technician(db_session: Session):
    tech = Person(first_name="Field", last_name="Tech", email="field-tech@example.com")
    customer = Person(
        first_name="Jane",
        last_name="Customer",
        email="jane.customer@example.com",
        phone="+15550001111",
        address_line1="Fallback House 1",
        city="Abuja",
        region="FCT",
        postal_code="900001",
    )
    db_session.add_all([tech, customer])
    db_session.commit()
    db_session.refresh(tech)
    db_session.refresh(customer)

    subscriber = Subscriber(
        person_id=customer.id,
        service_address_line1="12 Service Road",
        service_city="Lagos",
        service_region="LA",
        service_postal_code="100001",
    )
    db_session.add(subscriber)
    db_session.commit()
    db_session.refresh(subscriber)

    work_order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(
            title="Install CPE",
            subscriber_id=subscriber.id,
            assigned_to_person_id=tech.id,
            scheduled_start=datetime(2026, 3, 15, 9, 30, tzinfo=UTC),
        ),
    )

    notes = (
        db_session.query(Notification)
        .filter(Notification.channel == NotificationChannel.push)
        .filter(Notification.recipient == "field-tech@example.com")
        .filter(Notification.subject.like("%Install CPE%"))
        .all()
    )

    assert len(notes) == 1
    assert "assigned this work order as the technician" in (notes[0].body or "")
    assert "Customer: Jane Customer" in (notes[0].body or "")
    assert "Site: 12 Service Road, Lagos, LA, 100001" in (notes[0].body or "")
    assert "Start: 2026-03-15 09:30" in (notes[0].body or "")
    assert "Phone: +15550001111" in (notes[0].body or "")
    assert f"/admin/operations/work-orders/{work_order.id}" in (notes[0].body or "")


def test_work_order_reassignment_notifies_new_technician_only(db_session: Session):
    first = Person(first_name="First", last_name="Tech", email="first-tech@example.com")
    second = Person(first_name="Second", last_name="Tech", email="second-tech@example.com")
    db_session.add_all([first, second])
    db_session.commit()
    db_session.refresh(first)
    db_session.refresh(second)

    work_order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(
            title="Repair Fiber Drop",
            assigned_to_person_id=first.id,
        ),
    )

    workforce_service.work_orders.update(
        db_session,
        str(work_order.id),
        WorkOrderUpdate(assigned_to_person_id=second.id),
    )

    first_notes = (
        db_session.query(Notification)
        .filter(Notification.channel == NotificationChannel.push)
        .filter(Notification.recipient == "first-tech@example.com")
        .filter(Notification.subject.like("%Repair Fiber Drop%"))
        .all()
    )
    second_notes = (
        db_session.query(Notification)
        .filter(Notification.channel == NotificationChannel.push)
        .filter(Notification.recipient == "second-tech@example.com")
        .filter(Notification.subject.like("%Repair Fiber Drop%"))
        .all()
    )

    assert len(first_notes) == 1
    assert len(second_notes) == 1
    assert f"/admin/operations/work-orders/{work_order.id}" in (second_notes[0].body or "")


def test_work_order_notification_falls_back_to_person_address_and_caps_length(db_session: Session):
    tech = Person(first_name="Fallback", last_name="Tech", email="fallback-tech@example.com")
    customer = Person(
        first_name="Long",
        last_name="Customer",
        email="long.customer@example.com",
        phone="+15559990000",
        address_line1="123 Very Long Street Name That Keeps Going",
        address_line2="Block B Apartment 45 With Extra Directions",
        city="Port Harcourt",
        region="Rivers",
        postal_code="500001",
    )
    db_session.add_all([tech, customer])
    db_session.commit()
    db_session.refresh(tech)
    db_session.refresh(customer)

    subscriber = Subscriber(person_id=customer.id)
    db_session.add(subscriber)
    db_session.commit()
    db_session.refresh(subscriber)

    long_title = "Emergency repair for unstable upstream link with repeated packet loss and onsite validation"
    workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(
            title=long_title,
            subscriber_id=subscriber.id,
            assigned_to_person_id=tech.id,
        ),
    )

    note = (
        db_session.query(Notification)
        .filter(Notification.channel == NotificationChannel.push)
        .filter(Notification.recipient == "fallback-tech@example.com")
        .filter(Notification.subject.like("%Emergency repair%"))
        .one()
    )

    assert "Site: 123 Very Long Street Name That Keeps Going" in (note.body or "")
    assert len(note.body or "") <= 320


def test_work_order_assignment_triggers_customer_notification(db_session: Session, monkeypatch):
    tech = Person(first_name="Notify", last_name="Tech", email="notify-tech@example.com")
    customer = Person(
        first_name="Notify",
        last_name="Customer",
        email="notify-customer@example.com",
    )
    db_session.add_all([tech, customer])
    db_session.commit()
    db_session.refresh(tech)
    db_session.refresh(customer)

    subscriber = Subscriber(person_id=customer.id)
    db_session.add(subscriber)
    db_session.commit()
    db_session.refresh(subscriber)

    calls: list[str] = []

    def _fake_send(_db, work_order_id: str) -> bool:
        calls.append(work_order_id)
        return True

    monkeypatch.setattr(
        "app.services.eta_notifications.send_technician_assigned_notification",
        _fake_send,
    )

    work_order = workforce_service.work_orders.create(
        db_session,
        WorkOrderCreate(
            title="Customer Notification",
            subscriber_id=subscriber.id,
            assigned_to_person_id=tech.id,
        ),
    )

    assert calls == [str(work_order.id)]
