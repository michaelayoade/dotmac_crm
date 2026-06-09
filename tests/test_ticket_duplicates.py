from app.models.tickets import TicketStatus
from app.schemas.tickets import TicketCreate
from app.services import tickets as tickets_service


def test_unassigned_status_alone_does_not_flag_duplicate(db_session):
    tickets_service.tickets.create(
        db_session,
        TicketCreate(
            title="Karasana OLT 1 Port 1",
            description="Pon 1 customers affected",
            ticket_type="Cabinet Disconnection",
            status=TicketStatus.open,
        ),
    )

    result = tickets_service.find_duplicate_ticket_candidates(
        db_session,
        tickets_service.TicketDuplicateInput(
            title="GPON-JABI-3-pon-8 & 4",
            description="jabi",
            ticket_type="Cabinet Disconnection",
        ),
    )

    assert result.matches == []


def test_unassigned_ticket_can_match_on_strong_issue_content(db_session):
    existing = tickets_service.tickets.create(
        db_session,
        TicketCreate(
            title="GPON JABI PON 8 and 4 outage",
            description="Customers on GPON JABI PON 8 and 4 are down",
            ticket_type="Cabinet Disconnection",
            status=TicketStatus.open,
        ),
    )

    result = tickets_service.find_duplicate_ticket_candidates(
        db_session,
        tickets_service.TicketDuplicateInput(
            title="GPON JABI PON 8 and 4 outage",
            description="Customers on GPON JABI PON 8 and 4 are down",
            ticket_type="Cabinet Disconnection",
        ),
    )

    assert [match.ticket_id for match in result.matches] == [str(existing.id)]
    assert "unassigned active ticket has a similar issue" not in result.matches[0].reasons


def test_different_base_stations_do_not_match_on_generic_cabinet_text(db_session):
    tickets_service.tickets.create(
        db_session,
        TicketCreate(
            title="Karasana OLT 1 Port 1",
            description="Cabinet link disconnection.",
            ticket_type="Cabinet Disconnection",
            status=TicketStatus.open,
            metadata_={"base_station_details": "Karasana OLT 1 (Port 1)"},
        ),
    )

    result = tickets_service.find_duplicate_ticket_candidates(
        db_session,
        tickets_service.TicketDuplicateInput(
            title="SPDC OLT Port 5",
            description="Cabinet link disconnection.",
            ticket_type="Cabinet Disconnection",
            base_station_details="SPDC OLT (PORT 5)",
        ),
    )

    assert result.matches == []


def test_same_base_station_is_duplicate_signal(db_session):
    existing = tickets_service.tickets.create(
        db_session,
        TicketCreate(
            title="SPDC OLT Port 5",
            description="Cabinet link disconnection.",
            ticket_type="Cabinet Disconnection",
            status=TicketStatus.open,
            metadata_={"base_station_details": "SPDC OLT (PORT 5)"},
        ),
    )

    result = tickets_service.find_duplicate_ticket_candidates(
        db_session,
        tickets_service.TicketDuplicateInput(
            title="SPDC OLT Port 5",
            description="Cabinet link disconnection.",
            ticket_type="Cabinet Disconnection",
            base_station_details="spdc olt port 5",
        ),
    )

    assert [match.ticket_id for match in result.matches] == [str(existing.id)]
    assert "same base station" in result.matches[0].reasons
