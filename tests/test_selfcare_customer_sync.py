import uuid
from unittest.mock import patch

from app.models.person import PartyStatus, Person
from app.models.projects import Project
from app.models.sales_order import SalesOrder
from app.models.subscriber import Subscriber
from app.services.events.handlers.selfcare_customer import SelfcareCustomerHandler
from app.services.events.types import Event, EventType
from app.services.selfcare import build_customer_payload
from app.services.subscriber import subscriber as subscriber_service


def _make_person(db_session, **overrides) -> Person:
    data = {
        "first_name": "Test",
        "last_name": "Customer",
        "email": f"selfcare-{uuid.uuid4().hex[:12]}@example.com",
        "party_status": PartyStatus.lead,
    }
    data.update(overrides)
    person = Person(**data)
    db_session.add(person)
    db_session.commit()
    db_session.refresh(person)
    return person


def _make_project(db_session, *, owner_person_id=None, metadata_=None) -> Project:
    project = Project(
        name="Selfcare Fiber Install",
        owner_person_id=owner_person_id,
        metadata_=metadata_,
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


def _make_sales_order(db_session, person_id) -> SalesOrder:
    sales_order = SalesOrder(
        person_id=person_id,
        order_number=f"SO-{uuid.uuid4().hex[:8]}",
    )
    db_session.add(sales_order)
    db_session.commit()
    db_session.refresh(sales_order)
    return sales_order


def test_build_customer_payload_contains_selfcare_identity(db_session):
    person = _make_person(
        db_session,
        first_name="Ada",
        last_name="Lovelace",
        display_name="Ada Lovelace",
        phone="+2348000000000",
        city="Lagos",
    )

    payload = build_customer_payload(
        person,
        project_id="project-1",
        quote_id="quote-1",
        sales_order_id="sales-order-1",
    )

    assert payload["first_name"] == "Ada"
    assert payload["last_name"] == "Lovelace"
    assert payload["status"] == "new"
    assert payload["crm_person_id"] == str(person.id)
    assert payload["metadata"]["source"] == "dotmac_omni"
    assert payload["metadata"]["crm_project_id"] == "project-1"


@patch("app.services.events.handlers.selfcare_customer.ensure_person_customer")
@patch("app.services.events.handlers.selfcare_customer.create_customer")
def test_handler_creates_selfcare_customer_and_local_subscriber(mock_create, mock_ensure, db_session):
    person = _make_person(db_session)
    sales_order = _make_sales_order(db_session, person.id)
    project = _make_project(
        db_session,
        owner_person_id=person.id,
        metadata_={"sales_order_id": str(sales_order.id), "quote_id": str(uuid.uuid4())},
    )
    mock_create.return_value = "sc-123"

    event = Event(event_type=EventType.project_created, payload={}, project_id=project.id)

    SelfcareCustomerHandler().handle(db_session, event)

    mock_create.assert_called_once()
    mock_ensure.assert_called_once_with(db_session, person, "sc-123")
    subscriber = subscriber_service.get_by_external_id(db_session, "selfcare", "sc-123")
    assert subscriber is not None
    assert subscriber.person_id == person.id
    assert str(subscriber.sales_order_id) == str(sales_order.id)


@patch("app.services.events.handlers.selfcare_customer.create_customer")
def test_handler_reuses_existing_selfcare_id(mock_create, db_session):
    person = _make_person(db_session, metadata_={"selfcare_subscriber_id": "sc-existing"})
    project = _make_project(db_session, owner_person_id=person.id)

    event = Event(event_type=EventType.project_created, payload={}, project_id=project.id)

    SelfcareCustomerHandler().handle(db_session, event)

    mock_create.assert_not_called()
    subscriber = subscriber_service.get_by_external_id(db_session, "selfcare", "sc-existing")
    assert subscriber is not None
    db_session.refresh(person)
    assert person.party_status == PartyStatus.customer


def test_handler_ignores_non_project_event(db_session):
    person = _make_person(db_session)
    _make_project(db_session, owner_person_id=person.id)

    with patch("app.services.events.handlers.selfcare_customer.create_customer") as mock_create:
        event = Event(event_type=EventType.ticket_created, payload={})
        SelfcareCustomerHandler().handle(db_session, event)

    mock_create.assert_not_called()
    subscribers = db_session.query(Subscriber).filter(Subscriber.person_id == person.id).all()
    assert subscribers == []
