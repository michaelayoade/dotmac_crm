"""Field-efficiency additions: site contacts, visit history, open tickets, and
the urgent dispatch alert on customer reschedule."""

import uuid

from app.models.notification import Notification, NotificationChannel, NotificationStatus
from app.models.person import Person
from app.models.subscriber import Organization, Subscriber
from app.models.tickets import Ticket, TicketStatus
from app.models.workforce import WorkOrder, WorkOrderStatus, WorkOrderType
from app.services.field.jobs import _additional_contacts, _open_tickets, _recent_visits
from app.services.field.tracking import _notify_dispatch


def _person(db, org_id=None, phone=None) -> Person:
    p = Person(
        first_name="A",
        last_name="B",
        email=f"p-{uuid.uuid4().hex[:10]}@example.com",
        phone=phone,
        organization_id=org_id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _subscriber(db, person) -> Subscriber:
    s = Subscriber(person_id=person.id, external_system="selfcare", external_id=uuid.uuid4().hex[:8])
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def test_additional_contacts_lists_other_org_people(db_session):
    org = Organization(name="Acme Corp")
    db_session.add(org)
    db_session.commit()
    primary = _person(db_session, org_id=org.id)
    secondary = _person(db_session, org_id=org.id, phone="+2348010000000")
    sub = _subscriber(db_session, primary)

    contacts = _additional_contacts(sub)
    ids = {c["email"] for c in contacts}
    assert secondary.email in ids
    assert primary.email not in ids  # the primary contact isn't duplicated


def test_recent_visits_excludes_current_and_lists_completed(db_session):
    sub = _subscriber(db_session, _person(db_session))
    prior = WorkOrder(
        title="Prior install", subscriber_id=sub.id, status=WorkOrderStatus.completed, work_type=WorkOrderType.install
    )
    current = WorkOrder(title="Today repair", subscriber_id=sub.id, status=WorkOrderStatus.in_progress)
    db_session.add_all([prior, current])
    db_session.commit()

    visits = _recent_visits(db_session, sub, current.id)
    ids = {v["work_order_id"] for v in visits}
    assert prior.id in ids
    assert current.id not in ids  # current job isn't listed as history


def test_open_tickets_excludes_closed(db_session):
    sub = _subscriber(db_session, _person(db_session))
    open_t = Ticket(title="No sync", subscriber_id=sub.id, status=TicketStatus.open)
    closed_t = Ticket(title="Old issue", subscriber_id=sub.id, status=TicketStatus.closed)
    db_session.add_all([open_t, closed_t])
    db_session.commit()

    tickets = _open_tickets(db_session, sub)
    ids = {t["id"] for t in tickets}
    assert open_t.id in ids
    assert closed_t.id not in ids


def test_reschedule_notify_queues_real_email(db_session):
    tech = _person(db_session)
    wo = WorkOrder(title="Fix ONT", assigned_to_person_id=tech.id, status=WorkOrderStatus.scheduled)
    db_session.add(wo)
    db_session.commit()

    _notify_dispatch(db_session, wo, subject="Reschedule requested", body="...", urgent=True)
    db_session.commit()

    emails = (
        db_session.query(Notification)
        .filter(
            Notification.recipient == tech.email,
            Notification.channel == NotificationChannel.email,
            Notification.status == NotificationStatus.queued,
        )
        .all()
    )
    assert len(emails) == 1  # a real, sendable email was queued (not just an in-app row)
