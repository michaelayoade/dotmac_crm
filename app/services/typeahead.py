from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.models.dispatch import TechnicianProfile
from app.models.person import Person
from app.models.subscriber import Organization, Subscriber
from app.models.vendor import Vendor
from app.services.response import list_response


def people(db: Session, query: str, limit: int) -> list[dict]:
    term = (query or "").strip()
    if not term:
        results = db.query(Person).order_by(Person.created_at.desc()).limit(limit).all()
    else:
        like_term = f"%{term}%"
        results = (
            db.query(Person)
            .filter(
                or_(
                    Person.first_name.ilike(like_term),
                    Person.last_name.ilike(like_term),
                    Person.display_name.ilike(like_term),
                    Person.email.ilike(like_term),
                    Person.phone.ilike(like_term),
                )
            )
            .limit(limit)
            .all()
        )
    items = []
    for person in results:
        label = " ".join(part for part in [person.first_name, person.last_name] if part)
        if not label and person.display_name:
            label = person.display_name
        if person.email:
            label = f"{label} ({person.email})"
        elif person.phone:
            label = f"{label} ({person.phone})"
        items.append({"id": person.id, "label": label})
    return items


def people_response(db: Session, query: str, limit: int) -> dict:
    return list_response(people(db, query, limit), limit, 0)


def technicians(db: Session, query: str, limit: int) -> list[dict]:
    term = (query or "").strip()
    like_term = f"%{term}%"
    query_builder = (
        db.query(TechnicianProfile)
        .join(Person, TechnicianProfile.person_id == Person.id)
        .filter(TechnicianProfile.is_active.is_(True))
    )
    if term:
        query_builder = query_builder.filter(
            or_(
                Person.first_name.ilike(like_term),
                Person.last_name.ilike(like_term),
                Person.display_name.ilike(like_term),
                Person.email.ilike(like_term),
                Person.phone.ilike(like_term),
            )
        )
    results = query_builder.order_by(Person.first_name.asc(), Person.last_name.asc()).limit(limit).all()
    items = []
    for tech in results:
        person = tech.person
        if not person:
            continue
        label = " ".join(part for part in [person.first_name, person.last_name] if part)
        if not label and person.display_name:
            label = person.display_name
        if person.email:
            label = f"{label} ({person.email})"
        elif person.phone:
            label = f"{label} ({person.phone})"
        items.append({"id": person.id, "label": label})
    return items


def technicians_response(db: Session, query: str, limit: int) -> dict:
    return list_response(technicians(db, query, limit), limit, 0)


def subscribers(db: Session, query: str, limit: int) -> list[dict]:
    """Search subscribers by subscriber number, account number, name, or external ID."""
    term = (query or "").strip()
    query_builder = (
        db.query(Subscriber)
        .outerjoin(Person, Subscriber.person_id == Person.id)
        .outerjoin(Organization, Subscriber.organization_id == Organization.id)
        .options(
            joinedload(Subscriber.person),
            joinedload(Subscriber.organization),
        )
    )
    if not term:
        results = query_builder.order_by(Subscriber.created_at.desc()).limit(limit).all()
    else:
        like_term = f"%{term}%"
        results = (
            query_builder.filter(
                or_(
                    Subscriber.subscriber_number.ilike(like_term),
                    Subscriber.account_number.ilike(like_term),
                    Subscriber.external_id.ilike(like_term),
                    Person.first_name.ilike(like_term),
                    Person.last_name.ilike(like_term),
                    Person.email.ilike(like_term),
                    Organization.name.ilike(like_term),
                )
            )
            .limit(limit)
            .all()
        )
    items = []
    for subscriber in results:
        label = subscriber.display_name
        if subscriber.subscriber_number:
            label = f"{label} ({subscriber.subscriber_number})"
        items.append({"id": subscriber.id, "label": label})
    return items


def subscribers_response(db: Session, query: str, limit: int) -> dict:
    return list_response(subscribers(db, query, limit), limit, 0)


def vendors(db: Session, query: str, limit: int) -> list[dict]:
    """Search vendors by name."""
    term = (query or "").strip()
    if not term:
        return []
    like_term = f"%{term}%"
    results = (
        db.query(Vendor)
        .filter(
            or_(
                Vendor.name.ilike(like_term),
                Vendor.code.ilike(like_term),
                Vendor.contact_name.ilike(like_term),
            )
        )
        .filter(Vendor.is_active.is_(True))
        .limit(limit)
        .all()
    )
    return [{"id": v.id, "label": v.name} for v in results]


def vendors_response(db: Session, query: str, limit: int) -> dict:
    return list_response(vendors(db, query, limit), limit, 0)


def organizations(db: Session, query: str, limit: int) -> list[dict]:
    """Search organizations by name."""
    term = (query or "").strip()
    if not term:
        return []
    like_term = f"%{term}%"
    results = (
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
    return [{"id": org.id, "label": org.name} for org in results]


def organizations_response(db: Session, query: str, limit: int) -> dict:
    return list_response(organizations(db, query, limit), limit, 0)


def network_devices_response(db: Session, query: str, limit: int) -> dict:
    """Search network devices by name."""
    from app.models.network import OLTDevice

    term = (query or "").strip()
    if not term:
        return list_response([], limit, 0)
    like_term = f"%{term}%"
    results = (
        db.query(OLTDevice)
        .filter(
            or_(
                OLTDevice.name.ilike(like_term),
                OLTDevice.hostname.ilike(like_term),
                OLTDevice.mgmt_ip.ilike(like_term),
            )
        )
        .limit(limit)
        .all()
    )
    items = [{"id": d.id, "label": d.name or d.hostname or d.mgmt_ip} for d in results]
    return list_response(items, limit, 0)


def pop_sites_response(db: Session, query: str, limit: int) -> dict:
    """Search POP sites by name."""
    from app.models.gis import GeoLocation, GeoLocationType

    term = (query or "").strip()
    if not term:
        return list_response([], limit, 0)
    like_term = f"%{term}%"
    results = (
        db.query(GeoLocation)
        .filter(
            or_(
                GeoLocation.name.ilike(like_term),
            )
        )
        .filter(GeoLocation.location_type == GeoLocationType.pop)
        .limit(limit)
        .all()
    )
    items = [{"id": p.id, "label": p.name} for p in results]
    return list_response(items, limit, 0)


def global_search(db: Session, query: str, limit_per_type: int = 3) -> dict:
    """
    Search across multiple entity types for global search suggestions.
    Returns categorized results with navigation URLs.
    """
    from app.models.tickets import Ticket
    from app.models.workforce import WorkOrder

    term = (query or "").strip()
    if not term or len(term) < 2:
        return {"categories": []}

    like_term = f"%{term}%"
    categories = []

    # Search people (customers)
    customer_results = (
        db.query(Person)
        .outerjoin(Organization, Person.organization_id == Organization.id)
        .options(joinedload(Person.organization))
        .filter(
            or_(
                Person.first_name.ilike(like_term),
                Person.last_name.ilike(like_term),
                Person.email.ilike(like_term),
                Organization.name.ilike(like_term),
            )
        )
        .limit(limit_per_type)
        .all()
    )
    if customer_results:
        categories.append(
            {
                "name": "Customers",
                "icon": "users",
                "items": [
                    {
                        "id": str(p.id),
                        "label": f"{p.first_name} {p.last_name}".strip() or p.email or "Person",
                        "url": f"/admin/crm/contacts/{p.id}",
                        "type": "customer",
                    }
                    for p in customer_results
                ],
            }
        )

    # Search tickets
    ticket_results = (
        db.query(Ticket)
        .filter(
            or_(
                Ticket.title.ilike(like_term),
                Ticket.description.ilike(like_term),
            )
        )
        .limit(limit_per_type)
        .all()
    )
    if ticket_results:
        categories.append(
            {
                "name": "Tickets",
                "icon": "ticket",
                "items": [
                    {
                        "id": str(t.id),
                        "label": t.title or f"Ticket {t.id}",
                        "url": f"/admin/support/tickets/{t.id}",
                        "type": "ticket",
                    }
                    for t in ticket_results
                ],
            }
        )

    # Search work orders
    work_order_results = (
        db.query(WorkOrder)
        .filter(
            or_(
                WorkOrder.title.ilike(like_term),
                WorkOrder.description.ilike(like_term),
            )
        )
        .limit(limit_per_type)
        .all()
    )
    if work_order_results:
        categories.append(
            {
                "name": "Work Orders",
                "icon": "wrench",
                "items": [
                    {
                        "id": str(wo.id),
                        "label": wo.title or f"Work Order {wo.id}",
                        "url": f"/admin/operations/work-orders/{wo.id}",
                        "type": "work_order",
                    }
                    for wo in work_order_results
                ],
            }
        )

    return {"categories": categories, "query": term}
