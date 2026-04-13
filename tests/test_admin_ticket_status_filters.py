from starlette.requests import Request

from app.models.service_team import ServiceTeam, ServiceTeamType
from app.models.tickets import TicketStatus
from app.queries.tickets import TicketQuery
from app.schemas.tickets import TicketCreate
from app.services import filter_preferences as preferences
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


def test_ticket_query_filters_by_region(db_session):
    garki_ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Garki outage", status=TicketStatus.open, region="Garki"),
    )
    tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Wuse outage", status=TicketStatus.open, region="Wuse"),
    )

    tickets = TicketQuery(db_session).by_region("Garki").all()

    assert [ticket.id for ticket in tickets] == [garki_ticket.id]


def test_ticket_query_filters_by_service_team_group(db_session):
    noc_team = ServiceTeam(name="NOC", team_type=ServiceTeamType.support, region="Garki")
    field_team = ServiceTeam(name="Field Ops", team_type=ServiceTeamType.field_service, region="Garki")
    db_session.add_all([noc_team, field_team])
    db_session.commit()

    noc_ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="NOC ticket", status=TicketStatus.open, service_team_id=noc_team.id),
    )
    tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Field ticket", status=TicketStatus.open, service_team_id=field_team.id),
    )

    tickets = TicketQuery(db_session).by_service_team(noc_team.id).all()

    assert [ticket.id for ticket in tickets] == [noc_ticket.id]


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

    monkeypatch.setattr("app.web.admin._auth_helpers.get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr("app.web.admin._auth_helpers.get_current_user", lambda _request: None)

    response = admin_tickets.tickets_list(
        request=_make_request(),
        db=db_session,
    )

    assert response.context["effective_status"] == "not_closed"
    assert [ticket.id for ticket in response.context["tickets"]] == [open_ticket.id]
    body = response.body.decode()
    assert "Not Closed" in body
    assert 'name="region"' in body
    assert 'name="group"' in body
    assert 'name="pm"' not in body
    assert 'name="spc"' not in body


def test_tickets_list_filters_by_region_and_group(monkeypatch, db_session):
    noc_team = ServiceTeam(name="NOC", team_type=ServiceTeamType.support, region="Garki")
    field_team = ServiceTeam(name="Field Ops", team_type=ServiceTeamType.field_service, region="Garki")
    db_session.add_all([noc_team, field_team])
    db_session.commit()

    matching_ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Matching ticket", status=TicketStatus.open, region="Garki", service_team_id=noc_team.id),
    )
    tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Wrong region", status=TicketStatus.open, region="Wuse", service_team_id=noc_team.id),
    )
    tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Wrong group", status=TicketStatus.open, region="Garki", service_team_id=field_team.id),
    )

    monkeypatch.setattr("app.web.admin._auth_helpers.get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr("app.web.admin._auth_helpers.get_current_user", lambda _request: None)

    response = admin_tickets.tickets_list(
        request=_make_request(),
        region="Garki",
        group=str(noc_team.id),
        db=db_session,
    )

    assert response.context["region"] == "Garki"
    assert response.context["group"] == str(noc_team.id)
    assert [ticket.id for ticket in response.context["tickets"]] == [matching_ticket.id]


def test_tickets_list_clear_filters_clears_saved_preference(monkeypatch, db_session, person):
    preferences.save_preference(
        db_session,
        person.id,
        preferences.TICKETS_PAGE.key,
        {"status": "open", "region": "Garki"},
    )
    monkeypatch.setattr("app.web.admin._auth_helpers.get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(
        "app.web.admin._auth_helpers.get_current_user",
        lambda _request: {"person_id": str(person.id), "roles": [], "permissions": []},
    )

    response = admin_tickets.tickets_list(
        request=_make_request(),
        clear_filters=True,
        db=db_session,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/admin/support/tickets"
    assert preferences.get_preference(db_session, person.id, preferences.TICKETS_PAGE.key) is None
