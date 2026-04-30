import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from starlette.requests import Request

from app.models.material_request import MaterialRequest, MaterialRequestStatus
from app.models.person import Person
from app.models.service_team import ServiceTeam, ServiceTeamType
from app.models.subscriber import Organization, Subscriber, SubscriberStatus
from app.models.tickets import TicketChannel, TicketLink, TicketMerge, TicketPriority, TicketStatus
from app.queries.tickets import TicketQuery
from app.schemas.tickets import TicketCreate
from app.services import filter_preferences as preferences
from app.services import tickets as tickets_service
from app.web.admin import tickets as admin_tickets


def _make_request(path: str = "/admin/support/tickets") -> Request:
    parsed = urlsplit(path)
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": parsed.path,
            "headers": [],
            "query_string": parsed.query.encode(),
        }
    )


def _streaming_body_text(response) -> str:
    body = getattr(response, "body", None)
    if body is not None:
        return body.decode() if isinstance(body, bytes) else str(body)

    async def _collect() -> bytes:
        chunks: list[bytes] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode())
        return b"".join(chunks)

    return asyncio.run(_collect()).decode()


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


def test_ticket_query_filters_by_created_range(db_session):
    older_ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Older ticket", status=TicketStatus.open),
    )
    newer_ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Newer ticket", status=TicketStatus.open),
    )
    older_ticket.created_at = datetime(2026, 4, 10, 9, 0, tzinfo=UTC)
    newer_ticket.created_at = datetime(2026, 4, 20, 9, 0, tzinfo=UTC)
    db_session.commit()

    tickets = (
        TicketQuery(db_session)
        .by_created_range(
            datetime(2026, 4, 15, tzinfo=UTC).date(),
            datetime(2026, 4, 25, tzinfo=UTC).date(),
        )
        .all()
    )

    assert [ticket.id for ticket in tickets] == [newer_ticket.id]


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


def test_tickets_list_filters_by_date_range(monkeypatch, db_session):
    older_ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Older ticket", status=TicketStatus.open),
    )
    matching_ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Matching ticket", status=TicketStatus.open),
    )
    older_ticket.created_at = datetime(2026, 4, 10, 12, 0, tzinfo=UTC)
    matching_ticket.created_at = datetime(2026, 4, 20, 12, 0, tzinfo=UTC)
    db_session.commit()

    monkeypatch.setattr("app.web.admin._auth_helpers.get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr("app.web.admin._auth_helpers.get_current_user", lambda _request: None)

    response = admin_tickets.tickets_list(
        request=_make_request("/admin/support/tickets?date_from=2026-04-15&date_to=2026-04-25"),
        date_from="2026-04-15",
        date_to="2026-04-25",
        db=db_session,
    )

    assert response.context["date_from"] == "2026-04-15"
    assert response.context["date_to"] == "2026-04-25"
    assert [ticket.id for ticket in response.context["tickets"]] == [matching_ticket.id]
    body = response.body.decode()
    assert 'name="date_from"' in body
    assert 'name="date_to"' in body


def test_tickets_list_rejects_invalid_date_range(monkeypatch, db_session):
    monkeypatch.setattr("app.web.admin._auth_helpers.get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr("app.web.admin._auth_helpers.get_current_user", lambda _request: None)

    try:
        admin_tickets.tickets_list(
            request=_make_request("/admin/support/tickets?date_from=2026-04-25&date_to=2026-04-15"),
            date_from="2026-04-25",
            date_to="2026-04-15",
            db=db_session,
        )
        raise AssertionError("Expected tickets_list to reject invalid date range")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
        assert "From date" in str(getattr(exc, "detail", ""))


def test_tickets_list_search_without_status_uses_all_statuses(monkeypatch, db_session, person):
    open_ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Cabinet disconnection open", status=TicketStatus.open),
    )
    closed_ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(
            title="Cabinet disconnection closed", status=TicketStatus.closed, closed_at=open_ticket.created_at
        ),
    )
    tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Different ticket", status=TicketStatus.open),
    )
    preferences.save_preference(
        db_session,
        person.id,
        preferences.TICKETS_PAGE.key,
        {"status": "closed"},
    )
    monkeypatch.setattr("app.web.admin._auth_helpers.get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(
        "app.web.admin._auth_helpers.get_current_user",
        lambda _request: {"person_id": str(person.id), "roles": [], "permissions": []},
    )

    response = admin_tickets.tickets_list(
        request=_make_request("/admin/support/tickets?search=cabinet+disconnection&order_by=created_at&order_dir=desc"),
        search="cabinet disconnection",
        order_by="created_at",
        order_dir="desc",
        db=db_session,
    )

    assert response.context["effective_status"] == ""
    assert [ticket.id for ticket in response.context["tickets"]] == [closed_ticket.id, open_ticket.id]
    assert preferences.get_preference(db_session, person.id, preferences.TICKETS_PAGE.key) == {
        "search": "cabinet disconnection",
        "order_by": "created_at",
        "order_dir": "desc",
    }


def test_tickets_list_saves_date_filters_in_preferences(monkeypatch, db_session, person):
    ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Visible ticket", status=TicketStatus.open),
    )
    ticket.created_at = datetime.now(UTC) - timedelta(days=1)
    db_session.commit()
    monkeypatch.setattr("app.web.admin._auth_helpers.get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(
        "app.web.admin._auth_helpers.get_current_user",
        lambda _request: {"person_id": str(person.id), "roles": [], "permissions": []},
    )

    admin_tickets.tickets_list(
        request=_make_request("/admin/support/tickets?date_from=2026-04-01&date_to=2026-04-30"),
        date_from="2026-04-01",
        date_to="2026-04-30",
        db=db_session,
    )

    assert preferences.get_preference(db_session, person.id, preferences.TICKETS_PAGE.key) == {
        "date_from": "2026-04-01",
        "date_to": "2026-04-30",
    }


def test_tickets_export_csv_respects_filters(monkeypatch, db_session):
    older_ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Older ticket", status=TicketStatus.open),
    )
    matching_ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Matching ticket", status=TicketStatus.open),
    )
    older_ticket.created_at = datetime(2026, 4, 10, 12, 0, tzinfo=UTC)
    matching_ticket.created_at = datetime(2026, 4, 20, 12, 0, tzinfo=UTC)
    db_session.commit()
    monkeypatch.setattr("app.web.admin._auth_helpers.get_current_user", lambda _request: None)

    response = admin_tickets.tickets_export_csv(
        request=_make_request("/admin/support/tickets/export.csv?date_from=2026-04-15&date_to=2026-04-25"),
        date_from="2026-04-15",
        date_to="2026-04-25",
        db=db_session,
    )

    body = _streaming_body_text(response)
    assert response.media_type == "text/csv"
    assert "Matching ticket" in body
    assert "Older ticket" not in body


def test_tickets_export_csv_appends_detail_fields_to_selected_columns(monkeypatch, db_session, person):
    organization = Organization(name="Dotmac Ltd")
    customer = Person(
        first_name="Ada",
        last_name="Lovelace",
        email="ada@example.com",
        phone="+234800000001",
        address_line1="1 Marina Road",
        city="Lagos",
        region="Lagos",
        organization=organization,
    )
    assignee = Person(first_name="Grace", last_name="Hopper", email="grace@example.com")
    project_manager = Person(first_name="Alan", last_name="Turing", email="alan@example.com")
    site_coordinator = Person(first_name="Katherine", last_name="Johnson", email="katherine@example.com")
    team = ServiceTeam(name="Field Ops", team_type=ServiceTeamType.field_service, region="Lagos")
    subscriber = Subscriber(
        person=customer,
        organization=organization,
        subscriber_number="SUB-001",
        account_number="ACC-001",
        status=SubscriberStatus.active,
        service_plan="Fiber Pro",
        service_speed="100/20 Mbps",
        service_address_line1="1 Marina Road",
        service_region="Lagos",
    )
    db_session.add_all([organization, customer, assignee, project_manager, site_coordinator, team, subscriber])
    db_session.commit()

    ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(
            title="Visible ticket",
            description="Customer reports intermittent outage",
            status=TicketStatus.open,
            priority=TicketPriority.urgent,
            ticket_type="Outage",
            channel=TicketChannel.phone,
            customer_person_id=customer.id,
            created_by_person_id=person.id,
            ticket_manager_person_id=project_manager.id,
            assistant_manager_person_id=site_coordinator.id,
            service_team_id=team.id,
            subscriber_id=subscriber.id,
            region="Lagos",
            metadata_={"base_station_details": "BTS-12"},
        ),
    )
    ticket.closed_at = datetime(2026, 4, 21, 15, 30, tzinfo=UTC)
    ticket.assigned_to_person_id = assignee.id
    db_session.add(
        MaterialRequest(
            ticket_id=ticket.id,
            requested_by_person_id=person.id,
            number="MR-001",
            status=MaterialRequestStatus.submitted,
        )
    )
    merged_source = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Merged source", status=TicketStatus.open),
    )
    db_session.add(
        TicketMerge(
            source_ticket_id=merged_source.id,
            target_ticket_id=ticket.id,
            reason="Duplicate outage report",
            merged_by_person_id=person.id,
        )
    )
    primary_outage = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Primary outage", status=TicketStatus.open),
    )
    sibling_ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Sibling outage ticket", status=TicketStatus.open),
    )
    db_session.add_all(
        [
            TicketLink(
                from_ticket_id=ticket.id,
                to_ticket_id=primary_outage.id,
                link_type=tickets_service.RELATED_OUTAGE_LINK_TYPE,
                created_by_person_id=person.id,
            ),
            TicketLink(
                from_ticket_id=sibling_ticket.id,
                to_ticket_id=primary_outage.id,
                link_type=tickets_service.RELATED_OUTAGE_LINK_TYPE,
                created_by_person_id=person.id,
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr("app.web.admin._auth_helpers.get_current_user", lambda _request: None)

    response = admin_tickets.tickets_export_csv(
        request=_make_request("/admin/support/tickets/export.csv?columns=ticket,actions"),
        columns="ticket,actions",
        db=db_session,
    )

    body = _streaming_body_text(response)
    header = body.splitlines()[0]
    assert "Ticket ID" in header
    assert "Title" in header
    assert "Description" in header
    assert "Status" in header
    assert "Priority" in header
    assert "Customer Email" in header
    assert "Customer Phone" in header
    assert "Customer Organization" in header
    assert "Customer Address" in header
    assert "Subscriber Number" in header
    assert "Account Number" in header
    assert "Subscriber Status" in header
    assert "Service Plan" in header
    assert "Service Speed" in header
    assert "Service Address" in header
    assert "Base Station Details" in header
    assert "Created By" in header
    assert "Assigned Group" in header
    assert "Project Manager" in header
    assert "Site Coordinator" in header
    assert "Closed" in header
    assert "Merged Ticket IDs" in header
    assert "Merge Reasons" in header
    assert "Primary Outage Ticket" in header
    assert "Other Outage Ticket IDs" in header
    assert "Material Request IDs" in header
    assert "Material Request Item Counts" in header
    assert "Material Request Statuses" in header
    assert "Opened" in header
    assert "Actions" not in header
    assert str(ticket.number or ticket.id) in body
    assert "Visible ticket" in body
    assert "Customer reports intermittent outage" in body
    assert "ada@example.com" in body
    assert "+234800000001" in body
    assert "Dotmac Ltd" in body
    assert "1 Marina Road, Lagos, Lagos" in body
    assert "SUB-001" in body
    assert "ACC-001" in body
    assert "Fiber Pro" in body
    assert "100/20 Mbps" in body
    assert "BTS-12" in body
    assert "Field Ops" in body
    assert "Alan Turing" in body
    assert "Katherine Johnson" in body
    assert "MR-001" in body
    assert "submitted" in body
    assert "Merged source" in body
    assert "Duplicate outage report" in body
    assert "Primary outage" in body
    assert "Sibling outage ticket" in body


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
