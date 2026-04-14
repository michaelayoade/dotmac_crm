import json
from datetime import UTC, datetime
from types import SimpleNamespace

from starlette.requests import Request

from app.services import billing_risk_reports as billing_risk_service
from app.web.admin import billing_risk as billing_risk_web
from app.web.admin import build_router


def test_admin_router_prefers_isolated_billing_risk_routes():
    router = build_router()
    matching = [
        route
        for route in router.routes
        if getattr(route, "path", "") == "/admin/reports/subscribers/billing-risk"
        and "GET" in getattr(route, "methods", set())
    ]

    assert matching
    assert matching[0].endpoint.__module__ == "app.web.admin.billing_risk"


def test_admin_router_exposes_customer_retention_tracker_from_billing_risk_module():
    router = build_router()
    matching = [
        route
        for route in router.routes
        if getattr(route, "path", "") == "/admin/customer-retention" and "GET" in getattr(route, "methods", set())
    ]

    assert matching
    assert matching[0].endpoint.__module__ == "app.web.admin.billing_risk"


def test_admin_router_matches_retention_engagements_before_customer_profile():
    router = build_router()
    paths = [getattr(route, "path", "") for route in router.routes if "GET" in getattr(route, "methods", set())]

    assert paths.index("/admin/customer-retention/engagements") < paths.index("/admin/customer-retention/{customer_id}")


def test_retention_rep_options_include_fixed_reps_without_team_rows():
    class EmptyResult:
        def all(self):
            return []

    class EmptyDb:
        def execute(self, _statement):
            return EmptyResult()

    labels = [option["label"] for option in billing_risk_web._retention_rep_options(EmptyDb())]

    assert labels == [
        "Abigail Tongov",
        "Chizaram Ogbonna",
        "Grace Moses",
        "Stephanie Mojekwu",
    ]


def test_retention_rep_options_suppress_enterprise_suffix_for_ejiro():
    class Result:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

    class Db:
        calls = 0

        def execute(self, _statement):
            self.calls += 1
            if self.calls == 1:
                return Result(
                    [
                        (
                            "person-1",
                            "Ejiro Onovwiona",
                            "Ejiro",
                            "Onovwiona",
                            "ejiro@example.com",
                            "Enterprise Sales",
                        )
                    ]
                )
            return Result([])

    options = billing_risk_web._retention_rep_options(Db())
    ejiro = next(option for option in options if option["label"] == "Ejiro Onovwiona")

    assert ejiro["team"] == ""


def test_subscriber_billing_risk_page_renders_from_isolated_module(monkeypatch):
    monkeypatch.setattr(billing_risk_web, "get_current_user", lambda _request: {"id": "test-user"})
    monkeypatch.setattr(billing_risk_web, "get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(billing_risk_web, "get_csrf_token", lambda _request: "csrf-token")
    monkeypatch.setattr(billing_risk_web, "_latest_subscriber_sync_at", lambda _db: datetime.now(UTC))
    monkeypatch.setattr(
        billing_risk_web,
        "_retention_rep_options",
        lambda _db: [{"value": "rep-1", "label": "Sales Rep", "team": "Enterprise sales"}],
    )
    monkeypatch.setattr(
        billing_risk_web,
        "_retention_engagements_by_customer",
        lambda _db, customer_ids: (
            {
                "12345": [
                    {
                        "id": "engagement-1",
                        "customerId": "12345",
                        "customerName": "Blocked Customer",
                        "outcome": "Promised to Pay",
                        "note": "Customer promised payment",
                        "followUp": "2000-01-01",
                        "rep": "Sales Rep",
                        "repPersonId": "rep-1",
                        "createdAt": "2026-04-14T10:00:00",
                    }
                ]
            }
            if "12345" in customer_ids
            else {}
        ),
    )
    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_table",
        lambda *_args, **kwargs: (
            [
                {
                    "name": "Blocked Customer",
                    "email": "blocked@example.com",
                    "phone": "+2348099991111",
                    "city": "Abuja",
                    "street": "12 Aminu Kano Crescent",
                    "area": "Maitama",
                    "plan": "Home Fiber 50Mbps",
                    "mrr_total": 42000.0,
                    "subscriber_status": "Suspended",
                    "risk_segment": "Suspended",
                    "billing_start_date": "2024-01-15",
                    "blocked_date": "2024-04-18",
                    "balance": 9200.0,
                    "billing_cycle": "monthly",
                    "last_transaction_date": "2024-03-01",
                    "expires_in": "15d",
                    "invoiced_until": "2024-03-31",
                    "days_since_last_payment": 18,
                    "days_past_due": 18,
                    "total_paid": 12000.0,
                    "days_to_due": -3,
                    "is_high_balance_risk": True,
                    "open_tickets": 2,
                    "closed_tickets": 5,
                    "total_tickets": 7,
                    "ticket_subscriber_id": "11111111-1111-1111-1111-111111111111",
                    "_subscriber_uuid": "11111111-1111-1111-1111-111111111111",
                }
            ]
            if kwargs.get("limit") == 6000 or kwargs.get("page_size") is not None
            else []
        ),
    )
    monkeypatch.setattr(
        billing_risk_service,
        "get_overdue_invoices_table",
        lambda *_args, **_kwargs: [{"total_balance_due": 2000.0}],
    )
    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_summary",
        lambda *_args, **_kwargs: {
            "total_at_risk": 1,
            "total_balance_exposure": 9200.0,
            "high_balance_risk_count": 1,
            "overdue_invoice_balance": 2000.0,
        },
    )
    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_segment_breakdown",
        lambda *_args, **_kwargs: [
            {
                "segment": "Suspended",
                "count": 1,
                "share_pct": 100.0,
                "balance": 9200.0,
                "high_balance_count": 1,
                "billing_mix": "Monthly (1)",
            }
        ],
    )
    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_aging_buckets",
        lambda *_args, **_kwargs: [{"label": "Blocked 8-30 Days", "count": 1}],
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/billing-risk",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )
    response = billing_risk_web.subscriber_billing_risk(request=request, db=SimpleNamespace())

    assert response.status_code == 200
    body = response.body.decode()
    assert "Subscriber Billing Risk" in body
    assert "Customer Retention Tracker" in body
    assert "Blocked Customer" in body
    assert "Blocked Date" in body
    assert "12 Aminu Kano Crescent" in body
    assert "Open 2" in body
    assert "Closed 5" in body
    assert "Total 7" in body
    assert "/admin/support/tickets?subscriber=11111111-1111-1111-1111-111111111111&status=not_closed" in body
    assert "engagement-note-suggestions" in body
    assert "Customer said will pay next week" in body
    assert 'id="billing-risk-search-button"' in body
    assert "'X-CSRF-Token': csrfToken()" in body


def test_subscriber_billing_risk_live_bucket_requests_keep_segment_filters(monkeypatch):
    monkeypatch.setattr(billing_risk_web, "get_current_user", lambda _request: {"id": "test-user"})
    monkeypatch.setattr(billing_risk_web, "get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(billing_risk_web, "get_csrf_token", lambda _request: "csrf-token")
    monkeypatch.setattr(billing_risk_web, "_latest_subscriber_sync_at", lambda _db: datetime.now(UTC))
    monkeypatch.setattr(
        billing_risk_web,
        "_retention_rep_options",
        lambda _db: [{"value": "rep-1", "label": "Sales Rep", "team": "Enterprise sales"}],
    )
    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_table",
        lambda *_args, **kwargs: (
            [
                {
                    "name": "Suspended Customer",
                    "email": "suspended@example.com",
                    "phone": "+2348099991111",
                    "city": "Abuja",
                    "area": "Maitama",
                    "plan": "Home Fiber 50Mbps",
                    "mrr_total": 42000.0,
                    "subscriber_status": "Suspended",
                    "risk_segment": "Suspended",
                    "billing_start_date": "2024-01-15",
                    "blocked_date": "2024-04-18",
                    "blocked_for_days": 18,
                    "balance": 9200.0,
                    "days_past_due": 18,
                    "is_high_balance_risk": True,
                }
            ]
            if kwargs.get("limit") == 6000
            else []
        ),
    )
    monkeypatch.setattr(billing_risk_service, "get_overdue_invoices_table", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_summary",
        lambda *_args, **_kwargs: {"total_at_risk": 1, "total_balance_exposure": 9200.0, "high_balance_risk_count": 1},
    )
    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_segment_breakdown",
        lambda *_args, **_kwargs: [{"segment": "Suspended", "count": 1, "share_pct": 100.0}],
    )
    monkeypatch.setattr(billing_risk_service, "get_billing_risk_aging_buckets", lambda *_args, **_kwargs: [])

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/billing-risk",
            "headers": [],
            "query_string": b"segment=suspended",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )
    response = billing_risk_web.subscriber_billing_risk(request=request, db=SimpleNamespace(), segment="suspended")

    body = response.body.decode()
    assert 'data-segments="suspended"' in body
    assert "params.append(&#39;segments&#39;, segment)" in body or "params.append('segments', segment)" in body


def test_billing_risk_search_rows_skip_live_enrichment(monkeypatch):
    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_table",
        lambda *_args, **_kwargs: [
            {
                "name": "Search Customer",
                "balance": 1000.0,
                "days_past_due": 8,
                "risk_segment": "Suspended",
            }
        ],
    )
    enrich_calls = []
    monkeypatch.setattr(billing_risk_service, "enrich_billing_risk_rows", lambda rows: enrich_calls.append(rows))

    rows, metrics, has_next = billing_risk_web._billing_risk_page_rows(
        SimpleNamespace(),
        due_soon_days=7,
        high_balance_only=False,
        segment=None,
        selected_segments=[],
        days_past_due=None,
        page=1,
        page_size=50,
        search="search customer",
        overdue_bucket="all",
    )

    assert rows[0]["name"] == "Search Customer"
    assert metrics["total_count"] == 1
    assert has_next is False
    assert enrich_calls == []


def test_customer_retention_tracker_renders_from_billing_risk_filters(monkeypatch):
    monkeypatch.setattr(billing_risk_web, "get_current_user", lambda _request: {"id": "test-user"})
    monkeypatch.setattr(billing_risk_web, "get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(billing_risk_web, "_latest_subscriber_sync_at", lambda _db: datetime.now(UTC))
    monkeypatch.setattr(
        billing_risk_web,
        "_retention_rep_options",
        lambda _db: [{"value": "rep-1", "label": "Sales Rep", "team": "Enterprise sales"}],
    )
    monkeypatch.setattr(
        billing_risk_web,
        "_retention_engagements_by_customer",
        lambda _db, customer_ids: (
            {
                "12345": [
                    {
                        "id": "engagement-1",
                        "customerId": "12345",
                        "customerName": "Blocked Customer",
                        "outcome": "Promised to Pay",
                        "note": "Customer promised payment",
                        "followUp": "2000-01-01",
                        "rep": "Sales Rep",
                        "repPersonId": "rep-1",
                        "createdAt": "2026-04-14T10:00:00",
                    }
                ]
            }
            if "12345" in customer_ids
            else {}
        ),
    )
    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_table",
        lambda *_args, **_kwargs: [
            {
                "name": "Blocked Customer",
                "_external_id": "12345",
                "subscriber_id": "12345",
                "email": "blocked@example.com",
                "phone": "+2348099991111",
                "city": "Abuja",
                "street": "12 Aminu Kano Crescent",
                "area": "Maitama",
                "plan": "Home Fiber 50Mbps",
                "subscriber_status": "Suspended",
                "risk_segment": "Suspended",
                "balance": 9200.0,
                "days_past_due": 18,
                "is_high_balance_risk": True,
            },
            {
                "name": "No Update Customer",
                "_external_id": "54321",
                "subscriber_id": "54321",
                "email": "no-update@example.com",
                "phone": "+2348099992222",
                "city": "Lagos",
                "street": "No update street",
                "area": "Ikeja",
                "plan": "Home Fiber 20Mbps",
                "subscriber_status": "Suspended",
                "risk_segment": "Suspended",
                "balance": 5000.0,
                "days_past_due": 12,
                "is_high_balance_risk": False,
            },
        ],
    )
    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_segment_breakdown",
        lambda _rows: [
            {
                "segment": "Suspended",
                "count": 1,
                "share_pct": 100.0,
                "balance": 9200.0,
                "high_balance_count": 1,
            }
        ],
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/customer-retention",
            "headers": [],
            "query_string": b"segment=suspended",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )
    response = billing_risk_web.customer_retention_tracker(
        request=request,
        db=SimpleNamespace(scalar=lambda _stmt: 1),
        segment="suspended",
    )

    assert response.status_code == 200
    body = response.body.decode()
    assert "Customer Retention Tracker" in body
    assert "Win-back Rate" in body
    assert "Blocked Customer" in body
    assert "Customer promised payment" in body
    assert "No Update Customer" not in body
    assert "Pipeline Stage" in body
    assert "Promised to Pay" in body
    assert "Follow-ups Due" in body
    assert "Marked date: 2000-01-01" in body
    assert "Follow up now." in body
    assert "Back to Billing Risk" in body
    assert "Flow" not in body


def test_subscriber_billing_risk_blocked_dates_returns_json(monkeypatch):
    monkeypatch.setattr(billing_risk_web, "get_current_user", lambda _request: {"id": "test-user"})
    monkeypatch.setattr(
        billing_risk_service,
        "get_live_blocked_dates",
        lambda external_ids: {"12345": "2024-04-18"} if external_ids == ["12345"] else {},
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/billing-risk/blocked-dates",
            "headers": [],
            "query_string": b"external_id=12345",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )
    response = billing_risk_web.subscriber_billing_risk_blocked_dates(request=request, external_id=["12345"])

    assert response.status_code == 200
    assert response.body == b'{"blocked_dates":{"12345":"2024-04-18"}}'


def test_subscriber_billing_risk_rows_returns_html(monkeypatch):
    monkeypatch.setattr(billing_risk_web, "get_current_user", lambda _request: {"id": "test-user"})
    monkeypatch.setattr(
        billing_risk_web,
        "_billing_risk_page_rows",
        lambda *_args, **_kwargs: (
            [
                {
                    "name": "Blocked Customer",
                    "phone": "+2348099991111",
                    "city": "Abuja",
                    "street": "12 Aminu Kano Crescent",
                    "area": "Maitama",
                    "plan": "Home Fiber 50Mbps",
                    "mrr_total": 42000.0,
                    "subscriber_status": "Suspended",
                    "risk_segment": "Suspended",
                    "billing_start_date": "2024-01-15",
                    "blocked_date": "2024-04-18",
                    "balance": 9200.0,
                    "open_tickets": 2,
                    "closed_tickets": 5,
                    "total_tickets": 7,
                    "ticket_subscriber_id": "11111111-1111-1111-1111-111111111111",
                    "_subscriber_uuid": "11111111-1111-1111-1111-111111111111",
                }
            ],
            {"total_count": 1, "total_balance": 9200.0, "avg_days_overdue": 48},
            True,
        ),
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/billing-risk/rows",
            "headers": [],
            "query_string": b"page=1&page_size=50&bucket=all",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )
    response = billing_risk_web.subscriber_billing_risk_rows(
        request=request,
        db=None,
        page=1,
        page_size=50,
        bucket="all",
    )

    assert response.status_code == 200
    body = response.body.decode()
    assert "Blocked Customer" in body
    assert "12 Aminu Kano Crescent" in body
    assert "Open 2" in body
    assert "Closed 5" in body
    assert "Total 7" in body
    assert "/admin/support/tickets?subscriber=11111111-1111-1111-1111-111111111111&status=closed" in body
    assert ">Balance<" not in body
    assert "Page 1" in body
    assert "billing-risk-metric-count" in body
    assert "Last Outcome" in body
    assert "View tracker" in body


def test_customer_retention_tracker_detail_renders_customer_profile(monkeypatch):
    monkeypatch.setattr(billing_risk_web, "get_current_user", lambda _request: {"id": "test-user"})
    monkeypatch.setattr(billing_risk_web, "get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(
        billing_risk_web,
        "_retention_rep_options",
        lambda _db: [{"value": "rep-1", "label": "Sales Rep", "team": "Enterprise sales"}],
    )
    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_table",
        lambda *_args, **_kwargs: [
            {
                "name": "Tracker Customer",
                "_external_id": "12345",
                "subscriber_id": "12345",
                "phone": "+2348099991111",
                "city": "Abuja",
                "area": "Maitama",
                "plan": "Home Fiber 50Mbps",
                "mrr_total": 42000.0,
                "subscriber_status": "Suspended",
                "risk_segment": "Suspended",
                "billing_start_date": "2024-01-15",
                "blocked_date": "2024-04-18",
                "blocked_for_days": 18,
                "balance": 9200.0,
                "next_bill_date": "2024-05-15",
                "open_tickets": 2,
                "closed_tickets": 5,
                "total_tickets": 7,
                "ticket_subscriber_id": "11111111-1111-1111-1111-111111111111",
                "_subscriber_uuid": "11111111-1111-1111-1111-111111111111",
            }
        ],
    )
    monkeypatch.setattr(billing_risk_service, "enrich_billing_risk_rows", lambda rows: rows)
    monkeypatch.setattr(
        billing_risk_web,
        "_retention_engagements_by_customer",
        lambda _db, customer_ids: (
            {
                "12345": [
                    {
                        "id": "engagement-1",
                        "customerId": "12345",
                        "customerName": "Tracker Customer",
                        "outcome": "Promised to Pay",
                        "note": "Customer said payment will come on Friday",
                        "followUp": "2000-01-01",
                        "rep": "Sales Rep - Enterprise sales",
                        "repPersonId": "rep-1",
                        "createdAt": "2026-04-14T10:00:00",
                    }
                ]
            }
            if customer_ids == ["12345"]
            else {}
        ),
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/customer-retention/12345",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )
    response = billing_risk_web.customer_retention_tracker_detail(
        customer_id="12345",
        request=request,
        db=SimpleNamespace(),
        due_soon_days=7,
    )

    assert response.status_code == 200
    body = response.body.decode()
    assert "Customer Retention Profile" in body
    assert "Tracker Customer" in body
    assert "Sales Rep - Enterprise sales" in body
    assert "Customer said payment will come on Friday" in body
    assert "Promised to Pay" in body
    assert "2000-01-01" in body
    assert "Ticket Status" in body
    assert "Open 2" in body
    assert "Closed 5" in body
    assert "Total 7" in body
    assert "/admin/support/tickets?subscriber=11111111-1111-1111-1111-111111111111&status=not_closed" in body
    assert "Amount Owed" not in body


def test_customer_retention_engagements_returns_saved_history(monkeypatch):
    monkeypatch.setattr(billing_risk_web, "get_current_user", lambda _request: {"id": "test-user"})
    monkeypatch.setattr(
        billing_risk_web,
        "_retention_engagements_by_customer",
        lambda _db, customer_ids: (
            {
                "12345": [
                    {
                        "id": "engagement-1",
                        "customerId": "12345",
                        "customerName": "Tracker Customer",
                        "outcome": "Renewing",
                        "note": "Payment received",
                        "followUp": "",
                        "rep": "Sales Rep",
                        "repPersonId": "rep-1",
                        "createdAt": "2026-04-14T10:00:00",
                    }
                ]
            }
            if customer_ids == ["12345"]
            else {}
        ),
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/customer-retention/engagements",
            "headers": [],
            "query_string": b"customer_id=12345",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )
    response = billing_risk_web.customer_retention_engagements(
        request=request,
        db=SimpleNamespace(),
        customer_id=["12345"],
    )

    assert response.status_code == 200
    assert json.loads(response.body)["engagements"]["12345"][0]["note"] == "Payment received"
