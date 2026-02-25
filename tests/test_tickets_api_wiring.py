from app.api import tickets as tickets_api


def test_list_tickets_wires_search_before_created_by(monkeypatch, db_session):
    captured: dict[str, object] = {}

    def _fake_list_response(db, *args):
        captured["db"] = db
        captured["args"] = args
        return {"items": [], "count": 0, "limit": 25, "offset": 5}

    monkeypatch.setattr(tickets_api.tickets_service.tickets, "list_response", _fake_list_response)

    response = tickets_api.list_tickets(
        subscriber_id="sub-1",
        status="open",
        priority="high",
        channel="web",
        search="outage",
        created_by_person_id="creator-1",
        assigned_to_person_id="assignee-1",
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=25,
        offset=5,
        db=db_session,
    )

    assert response["count"] == 0
    assert captured["db"] is db_session
    assert captured["args"] == (
        "sub-1",
        "open",
        "high",
        "web",
        "outage",
        "creator-1",
        "assignee-1",
        True,
        "created_at",
        "desc",
        25,
        5,
    )


def test_auto_assign_ticket_manually_wires_actor(monkeypatch, db_session):
    captured: dict[str, object] = {}
    expected_ticket = {"id": "ticket-1"}

    def _fake_auto_assign_manual(db, ticket_id, actor_id=None):
        captured["db"] = db
        captured["ticket_id"] = ticket_id
        captured["actor_id"] = actor_id
        return expected_ticket

    monkeypatch.setattr(tickets_api.tickets_service.tickets, "auto_assign_manual", _fake_auto_assign_manual)

    response = tickets_api.auto_assign_ticket_manually(
        ticket_id="ticket-1",
        db=db_session,
        auth={"person_id": "person-1"},
    )

    assert response == expected_ticket
    assert captured["db"] is db_session
    assert captured["ticket_id"] == "ticket-1"
    assert captured["actor_id"] == "person-1"
