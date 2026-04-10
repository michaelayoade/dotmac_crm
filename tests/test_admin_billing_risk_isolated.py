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


def test_subscriber_billing_risk_page_renders_from_isolated_module(monkeypatch):
    monkeypatch.setattr(billing_risk_web, "get_current_user", lambda _request: {"id": "test-user"})
    monkeypatch.setattr(billing_risk_web, "get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(billing_risk_web, "get_csrf_token", lambda _request: "csrf-token")
    monkeypatch.setattr(billing_risk_web, "_latest_subscriber_sync_at", lambda _db: datetime.now(UTC))
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
    assert "Blocked Customer" in body
    assert "Blocked Date" in body


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
                    "area": "Maitama",
                    "plan": "Home Fiber 50Mbps",
                    "mrr_total": 42000.0,
                    "subscriber_status": "Suspended",
                    "risk_segment": "Suspended",
                    "billing_start_date": "2024-01-15",
                    "blocked_date": "2024-04-18",
                    "balance": 9200.0,
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
    assert "Page 1" in body
    assert "Total Blocked Customers" in body
