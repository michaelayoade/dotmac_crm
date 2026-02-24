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
