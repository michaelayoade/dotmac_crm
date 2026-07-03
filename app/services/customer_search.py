from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.person import Person
from app.models.subscriber import Organization, Subscriber
from app.models.tickets import Ticket
from app.models.workforce import WorkOrder
from app.services.response import list_response


def search(db: Session, query: str, limit: int = 20) -> list[dict]:
    term = (query or "").strip()
    if not term:
        return []
    like_term = f"%{term}%"
    people = (
        db.query(Person)
        .filter(
            or_(
                Person.first_name.ilike(like_term),
                Person.last_name.ilike(like_term),
                Person.email.ilike(like_term),
            )
        )
        .limit(limit)
        .all()
    )
    organizations = (
        db.query(Organization)
        .filter(
            or_(
                Organization.name.ilike(like_term),
                Organization.domain.ilike(like_term),
            )
        )
        .limit(limit)
        .all()
    )
    profiles = _person_profiles(db, people)
    items: list[dict] = []
    for person in people:
        label = f"{person.first_name} {person.last_name}"
        if person.email:
            label = f"{label} ({person.email})"
        items.append(
            {
                "id": person.id,
                "type": "person",
                "label": label,
                "ref": f"person:{person.id}",
                **profiles.get(person.id, {}),
            }
        )
    for org in organizations:
        label = org.name
        if org.domain:
            label = f"{label} ({org.domain})"
        items.append(
            {
                "id": org.id,
                "type": "organization",
                "label": label,
                "ref": f"organization:{org.id}",
            }
        )
    items.sort(key=lambda item: item["label"].lower())
    return items[:limit]


def search_response(db: Session, query: str, limit: int = 20) -> dict:
    items = search(db, query, limit)
    return list_response(items, limit, 0)


def _person_profiles(db: Session, people: list[Person]) -> dict[UUID, dict]:
    if not people:
        return {}
    person_ids = [person.id for person in people]
    subscribers = (
        db.query(Subscriber)
        .filter(Subscriber.person_id.in_(person_ids))
        .order_by(Subscriber.person_id.asc(), Subscriber.is_active.desc(), Subscriber.updated_at.desc())
        .all()
    )
    primary_by_person: dict[UUID, Subscriber] = {}
    person_by_subscriber: dict[UUID, UUID] = {}
    for subscriber in subscribers:
        if subscriber.person_id is None:
            continue
        person_by_subscriber[subscriber.id] = subscriber.person_id
        primary_by_person.setdefault(subscriber.person_id, subscriber)

    recent_jobs = _recent_jobs_by_person(db, person_by_subscriber)
    recent_tickets = _recent_tickets_by_person(db, person_ids, person_by_subscriber)
    profiles = {}
    for person in people:
        primary_subscriber = primary_by_person.get(person.id)
        profiles[person.id] = {
            "email": person.email,
            "phone": _best_phone(person),
            "address_text": _site_address(primary_subscriber, person)
            if primary_subscriber
            else _person_address(person),
            "account_status": _account_status(person, primary_subscriber),
            "service_plan": primary_subscriber.service_plan if primary_subscriber else None,
            "recent_jobs": recent_jobs.get(person.id, []),
            "recent_tickets": recent_tickets.get(person.id, []),
        }
    return profiles


def _best_phone(person: Person) -> str | None:
    if isinstance(person.phone, str) and person.phone.strip():
        return person.phone.strip()
    from app.services.person import PHONE_CHANNEL_TYPES

    for channel in person.channels or []:
        if channel.channel_type in PHONE_CHANNEL_TYPES and isinstance(channel.address, str) and channel.address.strip():
            return channel.address.strip()
    return None


def _site_address(subscriber: Subscriber, person: Person) -> str | None:
    service_parts = [
        subscriber.service_address_line1,
        subscriber.service_address_line2,
        subscriber.service_city,
        subscriber.service_region,
        subscriber.service_postal_code,
    ]
    text = ", ".join(part for part in service_parts if part) or None
    if text:
        return text
    return _person_address(person)


def _person_address(person: Person) -> str | None:
    parts = [
        person.address_line1,
        person.address_line2,
        person.city,
        person.region,
        person.postal_code,
    ]
    return ", ".join(part for part in parts if part) or None


def _account_status(person: Person, subscriber: Subscriber | None) -> str | None:
    if subscriber and subscriber.status:
        return subscriber.status.value if hasattr(subscriber.status, "value") else str(subscriber.status)
    if person.party_status:
        return person.party_status.value if hasattr(person.party_status, "value") else str(person.party_status)
    if person.status:
        return person.status.value if hasattr(person.status, "value") else str(person.status)
    return None


def _recent_jobs_by_person(db: Session, person_by_subscriber: dict[UUID, UUID]) -> dict[UUID, list[dict]]:
    if not person_by_subscriber:
        return {}
    subscriber_ids = list(person_by_subscriber)
    jobs = (
        db.query(WorkOrder)
        .filter(WorkOrder.subscriber_id.in_(subscriber_ids), WorkOrder.is_active.is_(True))
        .order_by(WorkOrder.updated_at.desc(), WorkOrder.created_at.desc())
        .limit(max(3, len(subscriber_ids) * 3))
        .all()
    )
    by_person: dict[UUID, list[dict]] = {}
    for job in jobs:
        if job.subscriber_id is None:
            continue
        person_id = person_by_subscriber.get(job.subscriber_id)
        if person_id is None:
            continue
        person_jobs = by_person.setdefault(person_id, [])
        if len(person_jobs) >= 3:
            continue
        person_jobs.append(
            {
                "id": str(job.id),
                "title": job.title,
                "status": job.status.value if hasattr(job.status, "value") else str(job.status),
            }
        )
    return by_person


def _recent_tickets_by_person(
    db: Session, person_ids: list[UUID], person_by_subscriber: dict[UUID, UUID]
) -> dict[UUID, list[dict]]:
    filters = [Ticket.customer_person_id.in_(person_ids), Ticket.created_by_person_id.in_(person_ids)]
    subscriber_ids = list(person_by_subscriber)
    if subscriber_ids:
        filters.append(Ticket.subscriber_id.in_(subscriber_ids))
    tickets = (
        db.query(Ticket)
        .filter(or_(*filters), Ticket.is_active.is_(True))
        .order_by(Ticket.updated_at.desc(), Ticket.created_at.desc())
        .limit(max(3, (len(person_ids) + len(subscriber_ids)) * 3))
        .all()
    )
    by_person: dict[UUID, list[dict]] = {}
    seen_by_person: dict[UUID, set[UUID]] = {}
    for ticket in tickets:
        subscriber_person_id = (
            person_by_subscriber.get(ticket.subscriber_id) if ticket.subscriber_id is not None else None
        )
        target_person_ids = {
            person_id
            for person_id in [
                ticket.customer_person_id,
                ticket.created_by_person_id,
                subscriber_person_id,
            ]
            if person_id is not None and person_id in person_ids
        }
        for person_id in target_person_ids:
            person_tickets = by_person.setdefault(person_id, [])
            seen = seen_by_person.setdefault(person_id, set())
            if ticket.id in seen or len(person_tickets) >= 3:
                continue
            seen.add(ticket.id)
            person_tickets.append(
                {
                    "id": str(ticket.id),
                    "title": ticket.title or "No subject",
                    "reference": ticket.number or str(ticket.id),
                    "status": ticket.status.value if hasattr(ticket.status, "value") else str(ticket.status),
                }
            )
    return by_person
