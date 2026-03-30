from starlette.requests import Request

from app.models.tickets import TicketStatus
from app.queries.tickets import TicketQuery
from app.schemas.tickets import TicketCreate
from app.services import tickets as tickets_service
from app.web.admin import tickets as admin_tickets


def _make_request(path: str = "/admin/support/tickets") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
        }
    )


def test_ticket_query_not_closed_excludes_terminal_statuses(db_session):
    open_ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Open ticket", status=TicketStatus.open),
    )
    closed_ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Closed ticket", status=TicketStatus.closed, closed_at=open_ticket.created_at),
    )
    canceled_ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Canceled ticket", status=TicketStatus.canceled),
    )
    merged_ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Merged ticket", status=TicketStatus.merged),
    )

    tickets = TicketQuery(db_session).not_closed_tickets().all()
    ticket_ids = {ticket.id for ticket in tickets}

    assert open_ticket.id in ticket_ids
    assert closed_ticket.id not in ticket_ids
    assert canceled_ticket.id not in ticket_ids
    assert merged_ticket.id not in ticket_ids


def test_tickets_list_defaults_to_not_closed(monkeypatch, db_session):
    open_ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Visible ticket", status=TicketStatus.open),
    )
    tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Hidden closed ticket", status=TicketStatus.closed, closed_at=open_ticket.created_at),
    )
    tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Hidden canceled ticket", status=TicketStatus.canceled),
    )

    monkeypatch.setattr("app.web.admin.get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr("app.web.admin.get_current_user", lambda _request: None)

    response = admin_tickets.tickets_list(
        request=_make_request(),
        db=db_session,
    )

    assert response.context["effective_status"] == "not_closed"
    assert [ticket.id for ticket in response.context["tickets"]] == [open_ticket.id]
    assert "Not Closed" in response.body.decode()
