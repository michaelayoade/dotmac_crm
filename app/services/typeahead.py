from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.sql.elements import ColumnElement

from app.models.dispatch import TechnicianProfile
from app.models.inventory import InventoryItem
from app.models.person import Person, PersonStatus
from app.models.subscriber import Organization, Subscriber
from app.models.vendor import Vendor
from app.services.external_systems import selfcare_subscriber_number_for_splynx_id
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


def _person_label(person: Person) -> str:
    label = " ".join(part for part in [person.first_name, person.last_name] if part)
    if not label and person.display_name:
        label = person.display_name
    if person.email:
        return f"{label} ({person.email})"
    if person.phone:
        return f"{label} ({person.phone})"
    return label


def ticket_people(db: Session, query: str, limit: int) -> list[dict]:
    """Search current ticket customers using person and authoritative subscriber identities."""
    term = (query or "").strip()
    current_people = db.query(Person).filter(
        Person.is_active.is_(True),
        Person.status != PersonStatus.archived,
    )
    if not term:
        people_rows = current_people.order_by(Person.created_at.desc()).limit(limit).all()
        return [{"id": person.id, "label": _person_label(person)} for person in people_rows]

    like_term = f"%{term}%"
    expected_number = selfcare_subscriber_number_for_splynx_id(term)
    people_rows = (
        current_people.filter(
            or_(
                Person.first_name.ilike(like_term),
                Person.last_name.ilike(like_term),
                Person.display_name.ilike(like_term),
                Person.email.ilike(like_term),
                Person.phone.ilike(like_term),
                Person.metadata_["selfcare_id"].as_string() == term,
                Person.metadata_["splynx_id"].as_string() == term,
            )
        )
        .limit(max(limit * 4, limit))
        .all()
    )

    subscriber_filters: list[ColumnElement[bool]] = [
        Subscriber.subscriber_number.ilike(like_term),
        Subscriber.account_number.ilike(like_term),
        Subscriber.external_id.ilike(like_term),
    ]
    if expected_number:
        subscriber_filters.append(Subscriber.subscriber_number == expected_number)
    subscriber_rows = (
        db.query(Subscriber)
        .join(Person, Subscriber.person_id == Person.id)
        .options(joinedload(Subscriber.person))
        .filter(
            Subscriber.is_active.is_(True),
            Person.is_active.is_(True),
            Person.status != PersonStatus.archived,
            or_(*subscriber_filters),
        )
        .limit(max(limit * 4, limit))
        .all()
    )

    people_by_id = {person.id: person for person in people_rows}
    subscribers_by_person: dict = {}
    for subscriber in subscriber_rows:
        if subscriber.person is None:
            continue
        people_by_id.setdefault(subscriber.person.id, subscriber.person)
        subscribers_by_person.setdefault(subscriber.person.id, []).append(subscriber)

    normalized = term.casefold()

    def rank(person: Person) -> tuple[int, str]:
        metadata = person.metadata_ if isinstance(person.metadata_, dict) else {}
        if normalized in {
            str(metadata.get("selfcare_id") or "").casefold(),
            str(metadata.get("splynx_id") or "").casefold(),
        }:
            return (0, _person_label(person).casefold())
        for subscriber in subscribers_by_person.get(person.id, []):
            identifiers = {
                str(subscriber.subscriber_number or "").casefold(),
                str(subscriber.account_number or "").casefold(),
                str(subscriber.external_id or "").casefold(),
            }
            if normalized in identifiers or (expected_number and subscriber.subscriber_number == expected_number):
                return (0, _person_label(person).casefold())
        fields = [
            person.first_name,
            person.last_name,
            person.display_name,
            person.email,
            person.phone,
        ]
        normalized_fields = [str(value or "").casefold() for value in fields]
        if normalized in normalized_fields:
            return (1, _person_label(person).casefold())
        if any(value.startswith(normalized) for value in normalized_fields):
            return (2, _person_label(person).casefold())
        return (3, _person_label(person).casefold())

    items = []
    for person in sorted(people_by_id.values(), key=rank)[:limit]:
        label = _person_label(person)
        matching_number = next(
            (
                subscriber.subscriber_number
                for subscriber in subscribers_by_person.get(person.id, [])
                if subscriber.subscriber_number
                and (
                    subscriber.subscriber_number == expected_number
                    or normalized in subscriber.subscriber_number.casefold()
                )
            ),
            None,
        )
        metadata = person.metadata_ if isinstance(person.metadata_, dict) else {}
        if matching_number:
            label = f"{label} · {matching_number}"
        elif normalized in {
            str(metadata.get("selfcare_id") or "").casefold(),
            str(metadata.get("splynx_id") or "").casefold(),
        }:
            label = f"{label} · ID {term}"
        items.append({"id": person.id, "label": label})
    return items


def ticket_people_response(db: Session, query: str, limit: int) -> dict:
    return list_response(ticket_people(db, query, limit), limit, 0)


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


def ticket_subscribers(db: Session, query: str, limit: int) -> list[dict]:
    """Search selectable current subscribers and rank canonical migrated IDs first."""
    term = (query or "").strip()
    query_builder = (
        db.query(Subscriber)
        .outerjoin(Person, Subscriber.person_id == Person.id)
        .outerjoin(Organization, Subscriber.organization_id == Organization.id)
        .options(
            joinedload(Subscriber.person),
            joinedload(Subscriber.organization),
        )
        .filter(Subscriber.is_active.is_(True))
    )
    if not term:
        results = query_builder.order_by(Subscriber.updated_at.desc()).limit(limit).all()
    else:
        like_term = f"%{term}%"
        expected_number = selfcare_subscriber_number_for_splynx_id(term)
        filters: list[ColumnElement[bool]] = [
            Subscriber.subscriber_number.ilike(like_term),
            Subscriber.account_number.ilike(like_term),
            Subscriber.external_id.ilike(like_term),
            Person.first_name.ilike(like_term),
            Person.last_name.ilike(like_term),
            Person.display_name.ilike(like_term),
            Person.email.ilike(like_term),
            Organization.name.ilike(like_term),
        ]
        if expected_number:
            filters.append(Subscriber.subscriber_number == expected_number)
        results = query_builder.filter(or_(*filters)).limit(max(limit * 4, limit)).all()
        normalized = term.casefold()

        def rank(subscriber: Subscriber) -> tuple[int, str]:
            identifiers = {
                str(subscriber.subscriber_number or "").casefold(),
                str(subscriber.account_number or "").casefold(),
                str(subscriber.external_id or "").casefold(),
            }
            exact = normalized in identifiers or (
                expected_number is not None and subscriber.subscriber_number == expected_number
            )
            return (0 if exact else 1, str(subscriber.subscriber_number or subscriber.external_id or subscriber.id))

        results = sorted(results, key=rank)[:limit]

    items = []
    for subscriber in results:
        current_person = (
            subscriber.person
            if subscriber.person and subscriber.person.is_active and subscriber.person.status != PersonStatus.archived
            else None
        )
        if current_person:
            label = " ".join(part for part in [current_person.first_name, current_person.last_name] if part)
        elif subscriber.organization:
            label = subscriber.organization.name
        else:
            label = "Subscriber"
        identifier = subscriber.subscriber_number or subscriber.account_number or subscriber.external_id
        if identifier:
            label = f"{label} ({identifier})"
        items.append({"id": subscriber.id, "label": label})
    return items


def ticket_subscribers_response(db: Session, query: str, limit: int) -> dict:
    return list_response(ticket_subscribers(db, query, limit), limit, 0)


def vendors(db: Session, query: str, limit: int) -> list[dict]:
    """Search vendors by name."""
    term = (query or "").strip()
    query_builder = db.query(Vendor).filter(Vendor.is_active.is_(True))

    if term:
        like_term = f"%{term}%"
        query_builder = query_builder.filter(
            or_(
                Vendor.name.ilike(like_term),
                Vendor.code.ilike(like_term),
                Vendor.contact_name.ilike(like_term),
            )
        )

    # When term is empty (e.g. typeahead focus), return a small list of active vendors.
    results = query_builder.order_by(Vendor.name.asc()).limit(limit).all()
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


def inventory_items(db: Session, query: str, limit: int) -> list[dict]:
    """Search active inventory items by name or SKU."""
    term = (query or "").strip()
    query_builder = db.query(InventoryItem).filter(InventoryItem.is_active.is_(True))
    if term:
        like_term = f"%{term}%"
        query_builder = query_builder.filter(
            or_(
                InventoryItem.name.ilike(like_term),
                InventoryItem.sku.ilike(like_term),
            )
        )
    results = query_builder.order_by(InventoryItem.name.asc()).limit(limit).all()
    items: list[dict] = []
    for inv in results:
        label = inv.name or ""
        if inv.sku:
            label = f"{label} ({inv.sku})"
        items.append({"id": inv.id, "label": label})
    return items


def inventory_items_response(db: Session, query: str, limit: int) -> dict:
    return list_response(inventory_items(db, query, limit), limit, 0)


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
