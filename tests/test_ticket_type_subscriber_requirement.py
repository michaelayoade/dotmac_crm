from fastapi import HTTPException

from app.models.tickets import TicketStatus
from app.schemas.tickets import TicketCreate
from app.services import tickets as tickets_service
from app.services.ticket_validation import _build_context, validate_ticket_creation


def test_ticket_type_requires_subscriber_for_configured_types(db_session):
    payload = TicketCreate(
        title="Router issue",
        ticket_type="Router Troubleshooting",
    )

    try:
        validate_ticket_creation(db_session, payload)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "Subscriber is required for the selected ticket type."
    else:
        raise AssertionError("Expected subscriber validation error")


def test_ticket_type_allows_submit_without_subscriber_for_other_types(db_session):
    payload = TicketCreate(
        title="Generic ticket",
        ticket_type="General Inquiry",
    )

    validate_ticket_creation(db_session, payload)


def test_ticket_type_requires_base_station_details_for_configured_types(db_session):
    payload = TicketCreate(
        title="Outage ticket",
        ticket_type="Multiple Cabinet Disconnection",
    )

    try:
        validate_ticket_creation(db_session, payload)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "Base station details are required for the selected ticket type."
    else:
        raise AssertionError("Expected base station validation error")


def test_ticket_type_requires_base_station_details_for_bts_outage(db_session):
    payload = TicketCreate(
        title="Outage ticket",
        ticket_type="BTS Outage",
    )

    try:
        validate_ticket_creation(db_session, payload)
    except HTTPException as exc:
        assert exc.status_code == 400
        assert exc.detail == "Base station details are required for the selected ticket type."
    else:
        raise AssertionError("Expected base station validation error")


def test_ticket_type_allows_base_station_required_type_when_details_present(db_session):
    payload = TicketCreate(
        title="Outage ticket",
        ticket_type="BTS Outage",
        metadata_={"base_station_details": "Tower A / Sector 2"},
    )

    validate_ticket_creation(db_session, payload)


def test_duplicate_override_omits_blocking_duplicate_context(db_session, person):
    existing = tickets_service.tickets.create(
        db_session,
        TicketCreate(
            title="Existing outage",
            customer_person_id=person.id,
            ticket_type="General Inquiry",
            status=TicketStatus.open,
        ),
    )

    blocked_payload = TicketCreate(
        title="New outage",
        customer_person_id=person.id,
        ticket_type="General Inquiry",
    )
    blocked_context = _build_context(db_session, blocked_payload)

    assert blocked_context["duplicate_ticket_id"] == str(existing.id)

    override_payload = TicketCreate(
        title="New outage",
        customer_person_id=person.id,
        ticket_type="General Inquiry",
        metadata_={"duplicate_override": True},
    )
    override_context = _build_context(db_session, override_payload)

    assert override_context["duplicate_override"] is True
    assert "duplicate_ticket_id" not in override_context
