from app.api import workflow as workflow_api


def test_list_ticket_assignment_rules_wiring(monkeypatch, db_session):
    captured: dict[str, object] = {}

    def _fake_list(db, strategy, is_active, order_by, order_dir, limit, offset):
        captured["args"] = (db, strategy, is_active, order_by, order_dir, limit, offset)
        return []

    monkeypatch.setattr(workflow_api.workflow_service.ticket_assignment_rules, "list", _fake_list)

    response = workflow_api.list_ticket_assignment_rules(
        strategy="round_robin",
        is_active=True,
        order_by="priority",
        order_dir="desc",
        limit=25,
        offset=5,
        db=db_session,
    )

    assert response["count"] == 0
    assert captured["args"] == (db_session, "round_robin", True, "priority", "desc", 25, 5)


def test_test_ticket_assignment_rule_wiring(monkeypatch, db_session):
    captured: dict[str, object] = {}
    expected = {
        "rule_id": "00000000-0000-0000-0000-000000000001",
        "ticket_id": "00000000-0000-0000-0000-000000000002",
        "matched": True,
        "strategy": "round_robin",
        "candidate_count": 1,
        "candidate_person_ids": ["00000000-0000-0000-0000-000000000003"],
        "preview_assignee_person_id": "00000000-0000-0000-0000-000000000003",
        "reason": "preview_ready",
    }

    def _fake_test_rule(db, rule_id, payload):
        captured["db"] = db
        captured["rule_id"] = rule_id
        captured["payload"] = payload
        return expected

    monkeypatch.setattr(workflow_api.workflow_service.ticket_assignment_rules, "test_rule", _fake_test_rule)

    payload = workflow_api.TicketAssignmentRuleTestRequest(
        ticket_ref="TCK-1001",
    )
    response = workflow_api.test_ticket_assignment_rule(
        "00000000-0000-0000-0000-000000000001",
        payload,
        db=db_session,
    )

    assert response == expected
    assert captured["db"] is db_session
    assert captured["rule_id"] == "00000000-0000-0000-0000-000000000001"
    assert captured["payload"] == payload


def test_get_sla_report_summary_wiring(monkeypatch, db_session):
    captured: dict[str, object] = {}
    expected = {
        "total_clocks": 0,
        "total_breaches": 0,
        "breach_rate": 0.0,
        "by_entity_type": [],
        "by_status": [],
        "ticket_by_service_team": [],
        "ticket_by_assignee": [],
    }

    def _fake_summary(db, start_at=None, end_at=None):
        captured["db"] = db
        captured["start_at"] = start_at
        captured["end_at"] = end_at
        return expected

    monkeypatch.setattr(workflow_api.workflow_service.sla_reports, "summary", _fake_summary)

    response = workflow_api.get_sla_report_summary(
        start_at="2026-02-01T00:00:00+00:00",
        end_at="2026-02-25T23:59:59+00:00",
        db=db_session,
    )

    assert response == expected
    assert captured["db"] is db_session
    assert str(captured["start_at"]).startswith("2026-02-01")
    assert str(captured["end_at"]).startswith("2026-02-25")


def test_get_sla_report_trend_wiring(monkeypatch, db_session):
    captured: dict[str, object] = {}
    expected = [
        {
            "date": "2026-02-24",
            "total": 5,
            "breached": 2,
            "breach_rate": 0.4,
        }
    ]

    def _fake_trend_daily(db, start_at=None, end_at=None):
        captured["db"] = db
        captured["start_at"] = start_at
        captured["end_at"] = end_at
        return expected

    monkeypatch.setattr(workflow_api.workflow_service.sla_reports, "trend_daily", _fake_trend_daily)

    response = workflow_api.get_sla_report_trend(
        start_at="2026-02-01T00:00:00+00:00",
        end_at="2026-02-25T23:59:59+00:00",
        db=db_session,
    )

    assert response == {"points": expected}
    assert captured["db"] is db_session
    assert str(captured["start_at"]).startswith("2026-02-01")
    assert str(captured["end_at"]).startswith("2026-02-25")
