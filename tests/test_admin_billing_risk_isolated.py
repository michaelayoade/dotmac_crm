import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from starlette.requests import Request

from app.models.subscriber import SubscriberBillingRiskSnapshot
from app.services import billing_risk_cache
from app.services import billing_risk_reports as billing_risk_service
from app.services import splynx as splynx_service
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

    assert "Abigail Tongov" in labels
    assert "Chizaram Ogbonna" in labels
    assert "Grace Moses" in labels
    assert "Stephanie Mojekwu" in labels
    assert "Ahmed Omodara" in labels
    assert "Chinelo Okoro" in labels


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
    row = {
        "name": "Blocked Customer",
        "_external_id": "12345",
        "email": "blocked@example.com",
        "phone": "+2348099991111",
        "city": "Abuja",
        "location": "Abuja HQ",
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
        "latest_ticket_ref": "20101",
        "ticket_subscriber_id": "11111111-1111-1111-1111-111111111111",
        "_subscriber_uuid": "11111111-1111-1111-1111-111111111111",
    }
    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_table",
        lambda *_args, **_kwargs: [row],
    )
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
    assert "Location" in body
    assert "Abuja HQ" in body
    assert "12 Aminu Kano Crescent" in body
    assert "Open 2" in body
    assert "Closed 5" in body
    assert "Total 7" in body
    assert "engagement-note-suggestions" in body
    assert "Customer said will pay next week" in body
    assert 'id="billing-risk-search-button"' in body
    assert "syncExportLink" in body
    assert "params.set(&#39;search&#39;" in body or "params.set('search'" in body
    assert "billing-risk-location-filter" in body
    assert "params.set(&#39;location&#39;" in body or "params.set('location'" in body
    assert "params.set(&#39;bucket&#39;" in body or "params.set('bucket'" in body
    assert "rowsNeedingRefresh" in body
    assert "params.set(&#39;mrr_sort&#39;" in body or "params.set('mrr_sort'" in body
    assert "downloadVisibleRowsCsv" in body
    assert "event.preventDefault()" in body
    assert "subscriber_billing_risk_visible_" in body
    assert "'X-CSRF-Token': csrfToken()" in body
    assert "Blocked 8-30 Days" in body


def test_billing_risk_live_rows_resolve_and_filter_location(monkeypatch):
    class Result:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class FakeDb:
        def execute(self, _statement):
            return Result([])

    customer_payload = {
        "id": "17060",
        "name": "Abduljabbar Anibilowo",
        "email": "abdul@example.com",
        "phone": "08012345678",
        "status": "blocked",
        "location_id": 1,
        "billing": {"blocking_date": "2026-04-01", "month_price": "42000"},
    }

    billing_risk_service.clear_live_splynx_cache()
    monkeypatch.setattr(splynx_service, "fetch_customers", lambda _db: [customer_payload])
    monkeypatch.setattr(splynx_service, "fetch_locations", lambda _db: [{"id": 1, "name": "Abuja"}])
    monkeypatch.setattr(splynx_service, "fetch_customer_internet_services", lambda _db, _external_id: [])
    monkeypatch.setattr(splynx_service, "fetch_customer_billing", lambda _db, _external_id: customer_payload["billing"])
    monkeypatch.setattr(
        splynx_service,
        "map_customer_to_subscriber_data",
        lambda _db, _customer, include_remote_details=False: {
            "status": "suspended",
            "subscriber_number": "100017060",
            "service_plan": "Home Fiber 50Mbps",
            "billing_cycle": "monthly",
            "sync_metadata": {},
        },
    )

    rows = billing_risk_service.get_billing_risk_table(
        FakeDb(),
        segment="suspended",
        location="Abuja",
        limit=10,
        enrich_visible_rows=False,
    )

    assert len(rows) == 1
    assert rows[0]["location"] == "Abuja"

    rows = billing_risk_service.get_billing_risk_table(
        FakeDb(),
        segment="suspended",
        location="CBD",
        limit=10,
        enrich_visible_rows=False,
    )

    assert rows == []


def test_billing_risk_cache_persists_and_filters_location(db_session):
    refreshed_at = datetime.now(UTC)
    db_session.add_all(
        [
            SubscriberBillingRiskSnapshot(
                id=uuid4(),
                external_system="splynx",
                external_id="100",
                name="Abuja Customer",
                city="Abuja",
                location="Abuja",
                risk_segment="Suspended",
                balance=1000,
                refreshed_at=refreshed_at,
            ),
            SubscriberBillingRiskSnapshot(
                id=uuid4(),
                external_system="splynx",
                external_id="200",
                name="SPDC Customer",
                city="Port Harcourt",
                location="SPDC",
                risk_segment="Suspended",
                balance=500,
                refreshed_at=refreshed_at,
            ),
        ]
    )
    db_session.commit()

    page = billing_risk_cache.list_cached_rows(db_session, location="Abuja")

    assert len(page.rows) == 1
    assert page.rows[0]["_external_id"] == "100"
    assert page.rows[0]["location"] == "Abuja"
    assert billing_risk_cache.location_options_cached(db_session) == ["Abuja", "SPDC"]


def test_billing_risk_cache_snapshot_values_include_location():
    values = billing_risk_cache._snapshot_values(
        {
            "_external_id": "300",
            "name": "Cached Customer",
            "location": "CBD",
            "risk_segment": "Due Soon",
            "balance": 100,
        },
        refreshed_at=datetime.now(UTC),
        subscribers_by_external={},
    )

    assert values["location"] == "CBD"


def test_subscriber_billing_risk_page_builds_table_once(monkeypatch):
    monkeypatch.setattr(billing_risk_web, "get_current_user", lambda _request: {"id": "test-user"})
    monkeypatch.setattr(billing_risk_web, "get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(billing_risk_web, "get_csrf_token", lambda _request: "csrf-token")
    monkeypatch.setattr(billing_risk_web, "_latest_subscriber_sync_at", lambda _db: datetime.now(UTC))
    monkeypatch.setattr(billing_risk_web, "_retention_rep_options", lambda _db: [])
    monkeypatch.setattr(billing_risk_web, "_retention_engagements_by_customer", lambda _db, customer_ids: {})

    calls = {"count": 0}
    row = {
        "name": "Blocked Customer",
        "_external_id": "12345",
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
        "blocked_for_days": 12,
        "balance": 9200.0,
        "days_past_due": 18,
    }

    def fake_table(*_args, **_kwargs):
        calls["count"] += 1
        return [row]

    monkeypatch.setattr(billing_risk_service, "get_billing_risk_table", fake_table)
    monkeypatch.setattr(billing_risk_service, "enrich_billing_risk_rows", lambda rows: rows)
    monkeypatch.setattr(billing_risk_service, "get_overdue_invoices_table", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_summary",
        lambda *_args, **_kwargs: {
            "total_at_risk": 1,
            "total_balance_exposure": 9200.0,
            "high_balance_risk_count": 1,
            "overdue_invoice_balance": 0.0,
        },
    )
    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_segment_breakdown",
        lambda *_args, **_kwargs: [{"segment": "Suspended", "count": 1, "share_pct": 100.0, "balance": 9200.0}],
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
    assert calls["count"] == 2


def test_subscriber_billing_risk_page_uses_single_cached_dataset_load(monkeypatch):
    monkeypatch.setattr(billing_risk_web, "get_current_user", lambda _request: {"id": "test-user"})
    monkeypatch.setattr(billing_risk_web, "get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(billing_risk_web, "get_csrf_token", lambda _request: "csrf-token")
    monkeypatch.setattr(billing_risk_web, "_latest_subscriber_sync_at", lambda _db: datetime.now(UTC))
    monkeypatch.setattr(billing_risk_web, "_retention_rep_options", lambda _db: [])
    monkeypatch.setattr(billing_risk_web, "_retention_engagements_by_customer", lambda _db, customer_ids: {})
    monkeypatch.setattr(billing_risk_web, "outreach_channel_target_options", lambda _db: [])
    monkeypatch.setattr(
        billing_risk_web,
        "settings",
        replace(billing_risk_web.settings, billing_risk_route_use_cache=True),
    )

    cached_calls = {"count": 0}
    row = {
        "name": "Blocked Customer",
        "_external_id": "12345",
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
        "blocked_for_days": 12,
        "balance": 9200.0,
        "days_past_due": 18,
    }

    def fake_list_cached_rows(*_args, **_kwargs):
        cached_calls["count"] += 1
        return billing_risk_cache.BillingRiskPage(
            rows=[row],
            page_metrics={"total_count": 1, "total_balance": 9200.0, "avg_days_overdue": 18},
            has_next=False,
        )

    monkeypatch.setattr(billing_risk_cache, "list_cached_rows", fake_list_cached_rows)
    monkeypatch.setattr(billing_risk_cache, "cache_metadata", lambda _db: {"row_count": 1})
    monkeypatch.setattr(billing_risk_cache, "location_options_cached", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        billing_risk_cache,
        "summary_cached",
        lambda *_args, **_kwargs: {
            "total_at_risk": 1,
            "total_balance_exposure": 9200.0,
            "high_balance_risk_count": 1,
            "overdue_invoice_balance": 0.0,
        },
    )
    monkeypatch.setattr(
        billing_risk_cache,
        "segment_breakdown_cached",
        lambda *_args, **_kwargs: [{"segment": "Suspended", "count": 1, "share_pct": 100.0, "balance": 9200.0}],
    )
    monkeypatch.setattr(
        billing_risk_cache,
        "aging_buckets_cached",
        lambda *_args, **_kwargs: [{"label": "Blocked 8-30 Days", "count": 1}],
    )
    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_table",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("live builder should not be used")),
    )
    monkeypatch.setattr(billing_risk_service, "enrich_billing_risk_rows", lambda rows: rows)
    monkeypatch.setattr(billing_risk_service, "get_overdue_invoices_table", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_summary",
        lambda *_args, **_kwargs: {
            "total_at_risk": 1,
            "total_balance_exposure": 9200.0,
            "high_balance_risk_count": 1,
            "overdue_invoice_balance": 0.0,
        },
    )
    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_segment_breakdown",
        lambda *_args, **_kwargs: [{"segment": "Suspended", "count": 1, "share_pct": 100.0, "balance": 9200.0}],
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
    response = billing_risk_web.subscriber_billing_risk(request=request, db=SimpleNamespace(query=lambda *_args, **_kwargs: None))

    assert response.status_code == 200
    assert cached_calls["count"] == 1


def test_subscriber_billing_risk_export_matches_visible_columns_and_filters(monkeypatch):
    captured_kwargs = {}

    def fake_table(_db, **kwargs):
        captured_kwargs.update(kwargs)
        return [
            {
                "name": "Blocked Customer",
                "_external_id": "12345",
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
                "blocked_for_days": 18,
                "open_tickets": 2,
                "closed_tickets": 5,
                "total_tickets": 7,
            }
        ]

    monkeypatch.setattr(billing_risk_service, "get_billing_risk_table", fake_table)
    monkeypatch.setattr(
        billing_risk_web,
        "_retention_engagements_by_customer",
        lambda _db, customer_ids: (
            {
                "12345": [
                    {
                        "outcome": "Promised to Pay",
                        "followUp": "2026-04-20",
                    }
                ]
            }
            if customer_ids == ["12345"]
            else {}
        ),
    )
    monkeypatch.setattr(
        billing_risk_web,
        "_csv_response",
        lambda data, filename: SimpleNamespace(status_code=200, media_type="text/csv", data=data, filename=filename),
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/billing-risk/export",
            "headers": [],
            "query_string": b"segments=suspended&search=blocked&bucket=8-30&enterprise_only=true&mrr_sort=desc",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )
    response = billing_risk_web.subscriber_billing_risk_export(
        request=request,
        db=SimpleNamespace(),
        due_soon_days=7,
        high_balance_only=False,
        segment=None,
        segments=["suspended"],
        search="blocked",
        bucket="8-30",
        enterprise_only=True,
        mrr_sort="desc",
    )

    assert captured_kwargs["search"] == "blocked"
    assert captured_kwargs["overdue_bucket"] == "8-30"
    assert captured_kwargs["segments"] == ["suspended"]
    assert captured_kwargs["enterprise_only"] is False
    assert captured_kwargs["mrr_sort"] == "desc"
    assert captured_kwargs["limit"] == 6000
    assert response.status_code == 200
    assert list(response.data[0]) == [
        "Name",
        "Phone",
        "City",
        "Street",
        "Area",
        "Plan",
        "MRR Total",
        "Status",
        "Risk Segment",
        "Billing Start Date",
        "Blocked Date",
        "Blocked For",
        "Tickets Open",
        "Tickets Closed",
        "Tickets Total",
        "Last Outcome",
        "Follow-up",
    ]
    assert response.data[0]["Name"] == "Blocked Customer"
    assert response.data[0]["Street"] == "12 Aminu Kano Crescent"
    assert response.data[0]["Blocked For"] == "Blocked for 18 days"
    assert response.data[0]["Tickets Open"] == 2
    assert response.data[0]["Last Outcome"] == "Promised to Pay"
    assert response.data[0]["Follow-up"] == "2026-04-20"
    assert "Balance" not in response.data[0]
    assert "Email" not in response.data[0]
    assert "Billing Cycle" not in response.data[0]


def test_subscriber_billing_risk_live_bucket_requests_keep_segment_filters(monkeypatch):
    monkeypatch.setattr(billing_risk_web, "get_current_user", lambda _request: {"id": "test-user"})
    monkeypatch.setattr(billing_risk_web, "get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(billing_risk_web, "get_csrf_token", lambda _request: "csrf-token")
    monkeypatch.setattr(billing_risk_web, "_latest_subscriber_sync_at", lambda _db: datetime.now(UTC))
    row = {
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
    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_table",
        lambda *_args, **_kwargs: [row],
    )
    monkeypatch.setattr(
        billing_risk_web,
        "_retention_rep_options",
        lambda _db: [{"value": "rep-1", "label": "Sales Rep", "team": "Enterprise sales"}],
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


def test_subscriber_billing_risk_segment_links_preserve_enterprise_filter(monkeypatch):
    monkeypatch.setattr(billing_risk_web, "get_current_user", lambda _request: {"id": "test-user"})
    monkeypatch.setattr(billing_risk_web, "get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(billing_risk_web, "get_csrf_token", lambda _request: "csrf-token")
    monkeypatch.setattr(billing_risk_web, "_latest_subscriber_sync_at", lambda _db: None)
    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_table",
        lambda *_args, **_kwargs: [
            {
                "name": "Enterprise Customer",
                "_external_id": "12345",
                "phone": "+2348099991111",
                "city": "Abuja",
                "street": "12 Aminu Kano Crescent",
                "area": "Maitama",
                "plan": "Enterprise Fiber",
                "mrr_total": 82000.0,
                "subscriber_status": "Suspended",
                "risk_segment": "Suspended",
                "billing_start_date": "2024-01-15",
                "blocked_date": "2024-04-18",
                "blocked_for_days": 18,
                "balance": 9200.0,
                "days_past_due": 18,
                "is_high_balance_risk": True,
            }
        ],
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
    monkeypatch.setattr(
        billing_risk_web,
        "_retention_rep_options",
        lambda _db: [{"value": "rep-1", "label": "Sales Rep", "team": "Enterprise sales"}],
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/billing-risk",
            "headers": [],
            "query_string": b"customer_segment=enterprise&mrr_sort=desc&bucket=8-30",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )
    response = billing_risk_web.subscriber_billing_risk(
        request=request,
        db=SimpleNamespace(),
        bucket="8-30",
        enterprise_only=True,
        mrr_sort="desc",
    )

    body = response.body.decode()
    assert "bucket=8-30" in body
    assert 'data-segment-filter="overdue"' in body
    assert 'data-segment-filter="suspended"' in body


def test_subscriber_billing_risk_rows_combines_bucket_segment_and_customer_segment(monkeypatch):
    monkeypatch.setattr(billing_risk_web, "get_current_user", lambda _request: {"id": "test-user"})

    captured_kwargs: dict[str, object] = {}

    def fake_table(*_args, **kwargs):
        captured_kwargs.update(kwargs)
        return [
            {
                "name": "Enterprise Suspended Customer",
                "_external_id": "12345",
                "phone": "+2348099991111",
                "city": "Abuja",
                "street": "12 Aminu Kano Crescent",
                "area": "Maitama",
                "plan": "Home Fiber 50Mbps",
                "mrr_total": 82000.0,
                "subscriber_status": "Suspended",
                "risk_segment": "Suspended",
                "billing_start_date": "2024-01-15",
                "blocked_date": "2024-04-18",
                "blocked_for_days": 12,
                "balance": 9200.0,
                "open_tickets": 2,
                "closed_tickets": 5,
                "total_tickets": 7,
                "latest_ticket_ref": "20101",
                "ticket_subscriber_id": "11111111-1111-1111-1111-111111111111",
                "_subscriber_uuid": "11111111-1111-1111-1111-111111111111",
            }
        ]

    monkeypatch.setattr(billing_risk_service, "get_billing_risk_table", fake_table)

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/billing-risk/rows",
            "headers": [],
            "query_string": b"page=1&page_size=50&bucket=8-30&segments=suspended&customer_segment=enterprise&mrr_sort=desc",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )
    response = billing_risk_web.subscriber_billing_risk_rows(
        request=request,
        db=None,
        segment=None,
        page=1,
        page_size=50,
        bucket="8-30",
        segments=["suspended"],
        customer_segment="enterprise",
        mrr_sort="desc",
    )

    assert response.status_code == 200
    assert captured_kwargs["overdue_bucket"] == "8-30"
    assert captured_kwargs["segments"] == ["suspended"]
    assert captured_kwargs["customer_segment"] == "all"
    assert captured_kwargs["enterprise_only"] is False
    assert captured_kwargs["mrr_sort"] == "desc"


def test_subscriber_billing_risk_rows_all_customers_overrides_stale_enterprise_flag(monkeypatch):
    monkeypatch.setattr(billing_risk_web, "get_current_user", lambda _request: {"id": "test-user"})

    captured_kwargs: dict[str, object] = {}

    def fake_table(*_args, **kwargs):
        captured_kwargs.update(kwargs)
        return [
            {
                "name": "General Customer",
                "_external_id": "12345",
                "phone": "+2348099991111",
                "city": "Abuja",
                "street": "12 Aminu Kano Crescent",
                "area": "Maitama",
                "plan": "Home Fiber 50Mbps",
                "mrr_total": 17500.0,
                "subscriber_status": "Suspended",
                "risk_segment": "Suspended",
                "billing_start_date": "2024-01-15",
                "blocked_date": "2024-04-18",
                "blocked_for_days": 12,
                "balance": 9200.0,
            }
        ]

    monkeypatch.setattr(billing_risk_service, "get_billing_risk_table", fake_table)

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/billing-risk/rows",
            "headers": [],
            "query_string": b"page=1&page_size=50&bucket=0-7&segments=suspended&customer_segment=&enterprise_only=true&mrr_sort=desc",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )
    response = billing_risk_web.subscriber_billing_risk_rows(
        request=request,
        db=None,
        segment=None,
        page=1,
        page_size=50,
        bucket="0-7",
        segments=["suspended"],
        customer_segment="",
        enterprise_only=True,
        mrr_sort="desc",
    )

    assert response.status_code == 200
    assert captured_kwargs["overdue_bucket"] == "0-7"
    assert captured_kwargs["segments"] == ["suspended"]
    assert captured_kwargs["customer_segment"] == "all"
    assert captured_kwargs["enterprise_only"] is False
    assert captured_kwargs["mrr_sort"] == "desc"


def test_subscriber_billing_risk_rows_non_enterprise_sets_customer_segment(monkeypatch):
    monkeypatch.setattr(billing_risk_web, "get_current_user", lambda _request: {"id": "test-user"})

    captured_kwargs: dict[str, object] = {}

    def fake_table(*_args, **kwargs):
        captured_kwargs.update(kwargs)
        return [
            {
                "name": "Non Enterprise Customer",
                "_external_id": "12345",
                "phone": "+2348099991111",
                "city": "Abuja",
                "street": "12 Aminu Kano Crescent",
                "area": "Maitama",
                "plan": "Home Fiber 20Mbps",
                "mrr_total": 17500.0,
                "subscriber_status": "Suspended",
                "risk_segment": "Suspended",
                "billing_start_date": "2024-01-15",
                "blocked_date": "2024-04-18",
                "blocked_for_days": 12,
                "balance": 9200.0,
            }
        ]

    monkeypatch.setattr(billing_risk_service, "get_billing_risk_table", fake_table)

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/billing-risk/rows",
            "headers": [],
            "query_string": b"page=1&page_size=50&bucket=0-7&segments=suspended&customer_segment=non_enterprise&mrr_sort=desc",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )
    response = billing_risk_web.subscriber_billing_risk_rows(
        request=request,
        db=None,
        segment=None,
        page=1,
        page_size=50,
        bucket="0-7",
        segments=["suspended"],
        customer_segment="non_enterprise",
        mrr_sort="desc",
    )

    assert response.status_code == 200
    assert captured_kwargs["overdue_bucket"] == "0-7"
    assert captured_kwargs["segments"] == ["suspended"]
    assert captured_kwargs["customer_segment"] == "all"
    assert captured_kwargs["enterprise_only"] is False
    assert captured_kwargs["mrr_sort"] == "desc"


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


def test_billing_risk_enterprise_filter_uses_cached_mrr_fallback(monkeypatch):
    monkeypatch.setattr(
        billing_risk_service,
        "_cached_live_splynx_read",
        lambda _cache_name, loader, *args, **kwargs: loader(),
    )
    monkeypatch.setattr(
        splynx_service,
        "fetch_customers",
        lambda *_args, **_kwargs: [
            {
                "id": "12345",
                "name": "Enterprise Customer",
                "email": "enterprise@example.com",
                "status": "suspended",
                "mrr_total": None,
                "billing": {"month_price": None},
            }
        ],
    )
    monkeypatch.setattr(
        splynx_service,
        "map_customer_to_subscriber_data",
        lambda *_args, **_kwargs: {
            "status": "suspended",
            "service_plan": "Enterprise Fiber",
            "balance": 1000.0,
            "sync_metadata": {"invoiced_until": "2026-04-01"},
        },
    )
    monkeypatch.setattr(splynx_service, "fetch_customer_billing", lambda *_args, **_kwargs: {"month_price": 80000})
    monkeypatch.setattr(splynx_service, "fetch_customer_internet_services", lambda *_args, **_kwargs: [])

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

        def scalars(self):
            return iter([])

    class FakeDb:
        def execute(self, statement):
            sql = str(statement)
            if "FROM person_channel" in sql:
                return FakeResult([])
            if "FROM person" in sql and "subscriber" not in sql:
                return FakeResult([])
            if "FROM subscriber" in sql:
                return FakeResult(
                    [
                        (
                            UUID("11111111-1111-1111-1111-111111111111"),
                            None,
                            "12345",
                            "SUB-001",
                            {},
                            None,
                            None,
                            1000.0,
                            "monthly",
                            "Enterprise Fiber",
                            "Abuja",
                            "Maitama",
                            "12 Aminu Kano Crescent",
                            None,
                            None,
                            None,
                            None,
                        )
                    ]
                )
            if "FROM ticket" in sql:
                return FakeResult([])
            return FakeResult([])

    rows = billing_risk_service.get_billing_risk_table(
        FakeDb(),
        enterprise_only=True,
        enrich_visible_rows=False,
    )

    assert len(rows) == 1
    assert rows[0]["name"] == "Enterprise Customer"
    assert rows[0]["mrr_total"] == 80000.0


def test_billing_risk_enterprise_filter_overrides_stale_cached_mrr(monkeypatch):
    class FakeSession:
        def close(self):
            return None

    monkeypatch.setattr(billing_risk_service, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(
        billing_risk_service,
        "_cached_live_splynx_read",
        lambda _cache_name, loader, *args, **kwargs: loader(),
    )
    monkeypatch.setattr(
        splynx_service,
        "fetch_customers",
        lambda *_args, **_kwargs: [
            {
                "id": "12345",
                "name": "Enterprise Customer",
                "email": "enterprise@example.com",
                "status": "suspended",
                "mrr_total": 17500,
                "billing": {"month_price": None},
            }
        ],
    )
    monkeypatch.setattr(
        splynx_service,
        "map_customer_to_subscriber_data",
        lambda *_args, **_kwargs: {
            "status": "suspended",
            "service_plan": "Enterprise Fiber",
            "balance": 1000.0,
            "sync_metadata": {"invoiced_until": "2026-04-01"},
        },
    )
    monkeypatch.setattr(splynx_service, "fetch_customer_billing", lambda *_args, **_kwargs: {"month_price": 80000})
    monkeypatch.setattr(splynx_service, "fetch_customer_internet_services", lambda *_args, **_kwargs: [])

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

        def scalars(self):
            return iter([])

    class FakeDb:
        def execute(self, statement):
            sql = str(statement)
            if "FROM person_channel" in sql:
                return FakeResult([])
            if "FROM person" in sql and "subscriber" not in sql:
                return FakeResult([])
            if "FROM subscriber" in sql:
                return FakeResult(
                    [
                        (
                            UUID("11111111-1111-1111-1111-111111111111"),
                            None,
                            "12345",
                            "SUB-001",
                            {},
                            None,
                            None,
                            1000.0,
                            "monthly",
                            "Enterprise Fiber",
                            "Abuja",
                            "Maitama",
                            "12 Aminu Kano Crescent",
                            17500.0,
                            None,
                            None,
                            None,
                        )
                    ]
                )
            if "FROM ticket" in sql:
                return FakeResult([])
            return FakeResult([])

    rows = billing_risk_service.get_billing_risk_table(
        FakeDb(),
        customer_segment="enterprise",
        enrich_visible_rows=False,
    )

    assert len(rows) == 1
    assert rows[0]["name"] == "Enterprise Customer"
    assert rows[0]["mrr_total"] == 80000.0


def test_all_customers_does_not_apply_enterprise_filter_when_customer_segment_is_explicit(monkeypatch):
    class FakeSession:
        def close(self):
            return None

    monkeypatch.setattr(billing_risk_service, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(
        billing_risk_service,
        "_cached_live_splynx_read",
        lambda _cache_name, loader, *args, **kwargs: loader(),
    )
    monkeypatch.setattr(
        splynx_service,
        "fetch_customers",
        lambda *_args, **_kwargs: [
            {
                "id": "12345",
                "name": "Standard Customer",
                "email": "standard@example.com",
                "status": "suspended",
                "mrr_total": 17500,
                "billing": {"month_price": None},
            }
        ],
    )
    monkeypatch.setattr(
        splynx_service,
        "map_customer_to_subscriber_data",
        lambda *_args, **_kwargs: {
            "status": "suspended",
            "service_plan": "Home Fiber",
            "balance": 1000.0,
            "sync_metadata": {"invoiced_until": "2026-04-01"},
        },
    )
    monkeypatch.setattr(splynx_service, "fetch_customer_billing", lambda *_args, **_kwargs: {"month_price": 80000})
    monkeypatch.setattr(splynx_service, "fetch_customer_internet_services", lambda *_args, **_kwargs: [])

    class FakeResult:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

        def scalars(self):
            return iter([])

    class FakeDb:
        def execute(self, statement):
            sql = str(statement)
            if "FROM person_channel" in sql:
                return FakeResult([])
            if "FROM person" in sql and "subscriber" not in sql:
                return FakeResult([])
            if "FROM subscriber" in sql:
                return FakeResult(
                    [
                        (
                            UUID("11111111-1111-1111-1111-111111111111"),
                            None,
                            "12345",
                            "SUB-001",
                            {},
                            None,
                            None,
                            1000.0,
                            "monthly",
                            "Home Fiber",
                            "Abuja",
                            "Maitama",
                            "12 Aminu Kano Crescent",
                            17500.0,
                            None,
                            None,
                            None,
                        )
                    ]
                )
            if "FROM ticket" in sql:
                return FakeResult([])
            return FakeResult([])

    rows = billing_risk_service.get_billing_risk_table(
        FakeDb(),
        customer_segment="all",
        enterprise_only=True,
        enrich_visible_rows=False,
    )

    assert len(rows) == 1
    assert rows[0]["name"] == "Standard Customer"
    assert rows[0]["mrr_total"] == 17500.0


def test_billing_risk_visible_enrichment_uses_splynx_billing_start_and_blocking_date(monkeypatch):
    class FakeSession:
        def close(self):
            return None

    billing_risk_service.clear_live_splynx_cache()
    monkeypatch.setattr(billing_risk_service, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(splynx_service, "fetch_customer_internet_services", lambda _db, _external_id: [])
    monkeypatch.setattr(
        splynx_service,
        "fetch_customer_billing",
        lambda _db, _external_id: {
            "billing_start_date": "2024-01-15",
            "blocking_date": "2024-04-10",
            "invoiced_until": "2024-04-18",
        },
    )

    rows = [
        {
            "name": "Blocked Customer",
            "_external_id": "12345",
            "subscriber_status": "Suspended",
            "risk_segment": "Suspended",
            "billing_start_date": "",
            "blocked_date": "",
            "balance": 9200.0,
            "mrr_total": 42000.0,
            "plan": "Home Fiber 50Mbps",
        }
    ]

    enriched = billing_risk_service.enrich_billing_risk_rows(rows)

    assert enriched[0]["billing_start_date"] == "2024-01-15"
    assert enriched[0]["invoiced_until"] == "2024-04-18"
    assert enriched[0]["blocked_date"] == "2024-04-10"
    assert isinstance(enriched[0]["blocked_for_days"], int)


def test_billing_risk_visible_enrichment_falls_back_to_invoiced_until_without_blocking_date(monkeypatch):
    class FakeSession:
        def close(self):
            return None

    billing_risk_service.clear_live_splynx_cache()
    monkeypatch.setattr(billing_risk_service, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(splynx_service, "fetch_customer_internet_services", lambda _db, _external_id: [])
    monkeypatch.setattr(
        splynx_service,
        "fetch_customer_billing",
        lambda _db, _external_id: {
            "billing_start_date": "2024-01-15",
            "invoiced_until": "2024-04-18",
        },
    )

    rows = [
        {
            "name": "Blocked Customer",
            "_external_id": "12345",
            "subscriber_status": "Suspended",
            "risk_segment": "Suspended",
            "billing_start_date": "",
            "blocked_date": "",
            "balance": 9200.0,
            "mrr_total": 42000.0,
            "plan": "Home Fiber 50Mbps",
        }
    ]

    enriched = billing_risk_service.enrich_billing_risk_rows(rows)

    assert enriched[0]["billing_start_date"] == "2024-01-15"
    assert enriched[0]["invoiced_until"] == "2024-04-18"
    assert enriched[0]["blocked_date"] == "2024-04-18"


def test_get_live_blocked_dates_prefers_splynx_blocking_date(monkeypatch):
    class FakeSession:
        def close(self):
            return None

    billing_risk_service.clear_live_splynx_cache()
    monkeypatch.setattr(billing_risk_service, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(
        splynx_service,
        "fetch_customer_billing",
        lambda _db, _external_id: {
            "blocking_date": "2024-01-01",
            "invoiced_until": "2024-04-18",
        },
    )

    assert billing_risk_service.get_live_blocked_dates(["12345"]) == {"12345": "2024-01-01"}


def test_get_live_blocked_dates_prefers_splynx_blocked_date_alias(monkeypatch):
    class FakeSession:
        def close(self):
            return None

    billing_risk_service.clear_live_splynx_cache()
    monkeypatch.setattr(billing_risk_service, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(
        splynx_service,
        "fetch_customer_billing",
        lambda _db, _external_id: {
            "blocked_date": "2024-02-02",
            "invoiced_until": "2024-04-18",
        },
    )

    assert billing_risk_service.get_live_blocked_dates(["12345"]) == {"12345": "2024-02-02"}


def test_get_live_blocked_dates_falls_back_to_splynx_invoiced_until(monkeypatch):
    class FakeSession:
        def close(self):
            return None

    billing_risk_service.clear_live_splynx_cache()
    monkeypatch.setattr(billing_risk_service, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(
        splynx_service,
        "fetch_customer_billing",
        lambda _db, _external_id: {
            "invoiced_until": "2024-04-18",
        },
    )

    assert billing_risk_service.get_live_blocked_dates(["12345"]) == {"12345": "2024-04-18"}


def test_get_live_blocked_dates_uses_service_blocking_date_when_billing_missing(monkeypatch):
    class FakeSession:
        def close(self):
            return None

    billing_risk_service.clear_live_splynx_cache()
    monkeypatch.setattr(billing_risk_service, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(
        splynx_service,
        "fetch_customer_billing",
        lambda _db, _external_id: {
            "invoiced_until": "2024-04-18",
        },
    )
    monkeypatch.setattr(
        splynx_service,
        "fetch_customer_internet_services",
        lambda _db, _external_id: [{"blocking_date": "2024-03-11"}],
    )

    assert billing_risk_service.get_live_blocked_dates(["12345"]) == {"12345": "2024-04-18"}


def test_get_live_blocked_dates_blocking_only_ids_skip_invoiced_until_fallback(monkeypatch):
    class FakeSession:
        def close(self):
            return None

    billing_risk_service.clear_live_splynx_cache()
    monkeypatch.setattr(billing_risk_service, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(
        splynx_service,
        "fetch_customer_billing",
        lambda _db, _external_id: {
            "invoiced_until": "2024-04-18",
        },
    )
    monkeypatch.setattr(
        splynx_service,
        "fetch_customer_internet_services",
        lambda _db, _external_id: [{"blocking_date": "2024-03-11"}],
    )

    assert billing_risk_service.get_live_blocked_dates(
        ["12345"],
        blocking_only_external_ids=["12345"],
    ) == {"12345": "2024-03-11"}


def test_get_live_blocked_dates_uses_customer_last_online_when_billing_and_service_missing(monkeypatch):
    class FakeSession:
        def close(self):
            return None

    billing_risk_service.clear_live_splynx_cache()
    monkeypatch.setattr(billing_risk_service, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(splynx_service, "fetch_customer_billing", lambda _db, _external_id: {})
    monkeypatch.setattr(splynx_service, "fetch_customer_internet_services", lambda _db, _external_id: [])
    monkeypatch.setattr(
        splynx_service,
        "fetch_customer",
        lambda _db, _external_id: {
            "status": "blocked",
            "last_online": "2026-03-20 11:28:03",
        },
    )

    assert billing_risk_service.get_live_blocked_dates(["12345"]) == {"12345": "2026-03-20"}


def test_get_live_blocked_dates_uses_prefetched_customers_payload(monkeypatch):
    class FakeSession:
        def close(self):
            return None

    billing_risk_service.clear_live_splynx_cache()
    monkeypatch.setattr(billing_risk_service, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(splynx_service, "fetch_customer_billing", lambda _db, _external_id: {})
    monkeypatch.setattr(splynx_service, "fetch_customer_internet_services", lambda _db, _external_id: [])
    monkeypatch.setattr(
        splynx_service,
        "fetch_customers",
        lambda _db: [
            {"id": "12345", "status": "blocked", "last_online": "2026-03-20 11:28:03"},
            {"id": "67890", "status": "blocked", "last_online": "2026-03-18 09:00:00"},
        ],
    )
    monkeypatch.setattr(
        splynx_service,
        "fetch_customer",
        lambda _db, _external_id: (_ for _ in ()).throw(AssertionError("should not call fetch_customer")),
    )

    assert billing_risk_service.get_live_blocked_dates(["12345", "67890"]) == {
        "12345": "2026-03-20",
        "67890": "2026-03-18",
    }


def test_get_live_blocked_dates_uses_primary_service_blocking_date_when_billing_missing(monkeypatch):
    class FakeSession:
        def close(self):
            return None

    billing_risk_service.clear_live_splynx_cache()
    monkeypatch.setattr(billing_risk_service, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(splynx_service, "fetch_customer_billing", lambda _db, _external_id: {})
    monkeypatch.setattr(
        splynx_service,
        "fetch_customer_internet_services",
        lambda _db, _external_id: [{"id": "1", "status": "blocked", "blocking_date": "2026-04-01"}],
    )
    monkeypatch.setattr(splynx_service, "fetch_customers", lambda _db: [])
    monkeypatch.setattr(splynx_service, "fetch_customer", lambda _db, _external_id: {})

    assert billing_risk_service.get_live_blocked_dates(["12345"], force_live=True) == {"12345": "2026-04-01"}


def test_get_live_blocked_dates_prefers_billing_blocking_date_over_customer_last_online(monkeypatch):
    class FakeSession:
        def close(self):
            return None

    billing_risk_service.clear_live_splynx_cache()
    monkeypatch.setattr(billing_risk_service, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(
        splynx_service,
        "fetch_customer_billing",
        lambda _db, _external_id: {"blocking_date": "2026-05-16"},
    )
    monkeypatch.setattr(splynx_service, "fetch_customer_internet_services", lambda _db, _external_id: [])
    monkeypatch.setattr(
        splynx_service,
        "fetch_customers",
        lambda _db: [{"id": "25678", "status": "active", "last_online": "2026-02-23 13:03:30"}],
    )
    monkeypatch.setattr(
        splynx_service,
        "fetch_customer",
        lambda _db, _external_id: {"status": "active", "last_online": "2026-02-23 13:03:30"},
    )

    assert billing_risk_service.get_live_blocked_dates(["25678"], force_live=True) == {"25678": "2026-05-16"}


def test_enrich_missing_blocked_fields_hides_blocked_for_for_active_due_soon(monkeypatch):
    monkeypatch.setattr(
        billing_risk_web,
        "_safe_live_blocked_dates",
        lambda _external_ids, force_live=False, blocking_only_external_ids=None: {"12345": "2026-05-16"},
    )
    rows = [
        {
            "_external_id": "12345",
            "subscriber_status": "Active",
            "risk_segment": "Due Soon",
            "blocked_date": "",
            "blocked_for_days": 12,
            "days_past_due": 20,
        }
    ]

    billing_risk_web._enrich_missing_blocked_fields(rows)

    assert rows[0]["blocked_date"] == "2026-05-16"
    assert rows[0]["blocked_for_days"] is None


def test_get_billing_risk_table_prefers_live_customer_status_date_over_local_updated_at(monkeypatch):
    class FakeSession:
        def close(self):
            return None

    class Result:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

        def scalars(self):
            return self._rows

    class FakeDb:
        def __init__(self):
            self.calls = 0

        def execute(self, _statement):
            self.calls += 1
            if self.calls == 1:
                return Result(
                    [
                        (
                            "sub-1",  # subscriber_id
                            None,  # person_id
                            "17060",  # external_id
                            "100017060",  # subscriber_number
                            {},  # sync_metadata
                            None,  # suspended_at
                            None,  # next_bill_date
                            0,  # balance
                            "",  # billing_cycle
                            "",  # service_plan
                            "",  # service_city
                            "",  # service_region
                            "",  # service_address_line1
                            None,  # activated_at
                            datetime(2026, 4, 13, tzinfo=UTC),  # updated_at (local sync timestamp)
                            datetime(2026, 1, 10, tzinfo=UTC),  # created_at
                            "",  # person_email
                        )
                    ]
                )
            return Result([])

    customer_payload = {
        "id": "17060",
        "name": "Abduljabbar Anibilowo",
        "email": "",
        "phone": "",
        "status": "blocked",
        "last_online": "2026-03-20 11:28:03",
        "billing": {"blocking_date": "0000-00-00"},
    }

    billing_risk_service.clear_live_splynx_cache()
    monkeypatch.setattr(billing_risk_service, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(splynx_service, "fetch_customers", lambda _db: [customer_payload])
    monkeypatch.setattr(splynx_service, "fetch_customer_internet_services", lambda _db, _external_id: [])
    monkeypatch.setattr(splynx_service, "fetch_customer_billing", lambda _db, _external_id: {})
    monkeypatch.setattr(
        splynx_service,
        "map_customer_to_subscriber_data",
        lambda _db, _customer, include_remote_details=False: {
            "status": "suspended",
            "subscriber_number": "100017060",
            "sync_metadata": {},
        },
    )

    rows = billing_risk_service.get_billing_risk_table(
        FakeDb(),
        segment="suspended",
        limit=10,
        enrich_visible_rows=False,
    )

    assert len(rows) == 1
    assert rows[0]["blocked_date"] == "2026-03-20"
    assert rows[0]["blocked_date"] != "2026-04-13"


def test_get_billing_risk_table_normalizes_street_display_symbols(monkeypatch):
    class FakeSession:
        def close(self):
            return None

    class Result:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

        def scalars(self):
            return self._rows

    class FakeDb:
        def __init__(self):
            self.calls = 0

        def execute(self, _statement):
            self.calls += 1
            if self.calls == 1:
                return Result(
                    [
                        (
                            "sub-1",
                            None,
                            "17060",
                            "100017060",
                            {},
                            None,
                            None,
                            0,
                            "",
                            "",
                            "",
                            "",
                            " 12,, Aminu   Kano;;; Crescent -- ",
                            None,
                            datetime(2026, 4, 13, tzinfo=UTC),
                            datetime(2026, 1, 10, tzinfo=UTC),
                            "",
                        )
                    ]
                )
            return Result([])

    customer_payload = {
        "id": "17060",
        "name": "Abduljabbar Anibilowo",
        "email": "",
        "phone": "",
        "status": "blocked",
        "last_online": "2026-03-20 11:28:03",
        "street_1": " 12,, Aminu   Kano;;; Crescent -- ",
        "street_2": "Suite 4  /  Block B",
        "billing": {"blocking_date": "0000-00-00"},
    }

    billing_risk_service.clear_live_splynx_cache()
    monkeypatch.setattr(billing_risk_service, "SessionLocal", lambda: FakeSession())
    monkeypatch.setattr(splynx_service, "fetch_customers", lambda _db: [customer_payload])
    monkeypatch.setattr(splynx_service, "fetch_customer_internet_services", lambda _db, _external_id: [])
    monkeypatch.setattr(splynx_service, "fetch_customer_billing", lambda _db, _external_id: {})
    monkeypatch.setattr(
        splynx_service,
        "map_customer_to_subscriber_data",
        lambda _db, _customer, include_remote_details=False: {
            "status": "suspended",
            "subscriber_number": "100017060",
            "sync_metadata": {},
        },
    )

    rows = billing_risk_service.get_billing_risk_table(
        FakeDb(),
        segment="suspended",
        limit=10,
        enrich_visible_rows=False,
    )

    assert len(rows) == 1
    assert rows[0]["street"] == "12, Aminu Kano; Crescent, Suite 4 / Block B"


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
        lambda _db, customer_ids: {
            customer_id: payload
            for customer_id, payload in {
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
                ],
                "88888": [
                    {
                        "id": "engagement-2",
                        "customerId": "88888",
                        "customerName": "Recovered Customer",
                        "outcome": "Renewing",
                        "note": "Account renewed",
                        "followUp": "",
                        "rep": "Sales Rep",
                        "repPersonId": "rep-1",
                        "createdAt": "2026-04-14T11:00:00",
                    }
                ],
                "99999": [
                    {
                        "id": "engagement-3",
                        "customerId": "99999",
                        "customerName": "Lost Customer",
                        "outcome": "Churning",
                        "note": "Confirmed cancellation",
                        "followUp": "",
                        "rep": "Sales Rep",
                        "repPersonId": "rep-1",
                        "createdAt": "2026-04-14T12:00:00",
                    }
                ],
            }.items()
            if customer_id in customer_ids
        },
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
            {
                "name": "Recovered Customer",
                "_external_id": "88888",
                "subscriber_id": "88888",
                "email": "recovered@example.com",
                "phone": "+2348099993333",
                "city": "Lagos",
                "street": "Recovery street",
                "area": "Ikeja",
                "plan": "Home Fiber 20Mbps",
                "subscriber_status": "Suspended",
                "risk_segment": "Due Soon",
                "balance": 1000.0,
                "days_past_due": 5,
                "is_high_balance_risk": False,
            },
            {
                "name": "Lost Customer",
                "_external_id": "99999",
                "subscriber_id": "99999",
                "email": "lost@example.com",
                "phone": "+2348099994444",
                "city": "Lagos",
                "street": "Churn street",
                "area": "Ikeja",
                "plan": "Home Fiber 20Mbps",
                "subscriber_status": "Suspended",
                "risk_segment": "Churned",
                "balance": 500.0,
                "days_past_due": 20,
                "is_high_balance_risk": False,
            },
            {
                "name": "Test",
                "_external_id": "11111",
                "subscriber_id": "11111",
                "email": "test@example.com",
                "phone": "+2348099995555",
                "city": "Abuja",
                "street": "Test street",
                "area": "Wuse",
                "plan": "Home Fiber 20Mbps",
                "subscriber_status": "Suspended",
                "risk_segment": "Suspended",
                "balance": 10.0,
                "days_past_due": 1,
                "is_high_balance_risk": False,
            },
            {
                "name": "Test Account",
                "_external_id": "22222",
                "subscriber_id": "22222",
                "email": "test-account@example.com",
                "phone": "+2348099996666",
                "city": "Abuja",
                "street": "Test account street",
                "area": "Wuse",
                "plan": "Home Fiber 20Mbps",
                "subscriber_status": "Suspended",
                "risk_segment": "Suspended",
                "balance": 10.0,
                "days_past_due": 1,
                "is_high_balance_risk": False,
            },
            {
                "name": "  Test-Account  ",
                "_external_id": "33333",
                "subscriber_id": "33333",
                "email": "test-account-variant@example.com",
                "phone": "+2348099997777",
                "city": "Abuja",
                "street": "Test variant street",
                "area": "Wuse",
                "plan": "Home Fiber 20Mbps",
                "subscriber_status": "Suspended",
                "risk_segment": "Suspended",
                "balance": 10.0,
                "days_past_due": 1,
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
    response = billing_risk_web.customer_retention_tracker(request=request, db=SimpleNamespace(), segment="suspended")

    assert response.status_code == 200
    body = response.body.decode()
    assert "Customer Retention Tracker" in body
    assert "Win-back Rate" in body
    assert "Recovered Customers (this period)" in body
    assert "Churn Rate" in body
    assert "Blocked Customer" in body
    assert "Customer promised payment" in body
    assert "Recovered Customer" in body
    assert "Lost Customer" in body
    assert "No Update Customer" not in body
    assert "Test" not in body
    assert "Test Account" not in body
    assert "Test-Account" not in body
    assert "Pipeline Stage" in body
    assert "Promised to Pay" in body
    assert "Follow-ups Due" in body
    assert "Marked date: 2000-01-01" in body
    assert "Follow up now." in body
    assert "Back to Billing Risk" in body
    assert "Flow" not in body


def test_subscriber_billing_risk_blocked_dates_returns_json(monkeypatch):
    monkeypatch.setattr(billing_risk_web, "get_current_user", lambda _request: {"id": "test-user"})
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        billing_risk_service,
        "get_live_blocked_dates",
        lambda external_ids, force_live=False, blocking_only_external_ids=None: (
            captured.update(
                {
                    "external_ids": external_ids,
                    "force_live": force_live,
                    "blocking_only_external_ids": blocking_only_external_ids,
                }
            )
            or ({"12345": "2024-04-18"} if external_ids == ["12345"] else {})
        ),
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
    response = billing_risk_web.subscriber_billing_risk_blocked_dates(
        request=request,
        external_id=["12345"],
        blocked_like_external_id=["12345"],
    )

    assert response.status_code == 200
    assert response.body == b'{"blocked_dates":{"12345":"2024-04-18"}}'
    assert captured["force_live"] is False
    assert captured["blocking_only_external_ids"] == ["12345"]


def test_subscriber_billing_risk_rows_returns_html(monkeypatch):
    monkeypatch.setattr(billing_risk_web, "get_current_user", lambda _request: {"id": "test-user"})
    captured_kwargs = {}

    def fake_table(*_args, **kwargs):
        captured_kwargs.update(kwargs)
        return [
            {
                "name": "Enterprise Blocked Customer",
                "_external_id": "12345",
                "phone": "+2348099991111",
                "city": "Abuja",
                "street": "12 Aminu Kano Crescent",
                "area": "Maitama",
                "plan": "Home Fiber 50Mbps",
                "mrr_total": 82000.0,
                "subscriber_status": "Suspended",
                "risk_segment": "Suspended",
                "billing_start_date": "2024-01-15",
                "blocked_date": "2024-04-18",
                "balance": 9200.0,
                "open_tickets": 2,
                "closed_tickets": 5,
                "total_tickets": 7,
                "latest_ticket_id": "19814",
                "ticket_status_counts": {"open": 2, "closed": 5, "pending": 1, "canceled": 1},
                "ticket_status_refs": {"open": "19814", "closed": "19815", "pending": "19816", "canceled": "19817"},
                "latest_ticket_ref": "20101",
                "ticket_subscriber_id": "11111111-1111-1111-1111-111111111111",
                "_subscriber_uuid": "11111111-1111-1111-1111-111111111111",
            }
        ]

    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_table",
        fake_table,
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/reports/subscribers/billing-risk/rows",
            "headers": [],
            "query_string": b"page=1&page_size=50&bucket=all&enterprise_only=true&mrr_sort=desc",
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
        enterprise_only=True,
        mrr_sort="desc",
    )

    assert response.status_code == 200
    body = response.body.decode()
    assert "Enterprise Blocked Customer" in body
    assert "Enterprise" in body
    assert "12 Aminu Kano Crescent" in body
    assert "Open 2" in body
    assert "Closed 5" in body
    assert "Pending 1" in body
    assert "Canceled 1" in body
    assert "Total 7" in body
    assert "/admin/support/tickets/19814" in body
    assert "/admin/support/tickets/19815" in body
    assert "/admin/support/tickets/19816" in body
    assert "/admin/support/tickets/19817" in body
    assert ">Balance<" not in body
    assert "Page 1" in body
    assert "billing-risk-metric-count" in body
    assert "Last Outcome" in body
    assert "View tracker" in body
    assert captured_kwargs["enterprise_only"] is False
    assert captured_kwargs["mrr_sort"] == "desc"


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


def test_customer_retention_tracker_uses_cached_customer_subset(monkeypatch):
    monkeypatch.setattr(billing_risk_web, "get_current_user", lambda _request: {"id": "test-user"})
    monkeypatch.setattr(billing_risk_web, "get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(billing_risk_web, "_latest_subscriber_sync_at", lambda _db: datetime.now(UTC))
    monkeypatch.setattr(billing_risk_web, "_retention_rep_options", lambda _db: [])
    monkeypatch.setattr(billing_risk_web, "outreach_channel_target_options", lambda _db: [])
    monkeypatch.setattr(
        billing_risk_web,
        "settings",
        replace(billing_risk_web.settings, customer_retention_route_use_cache=True),
    )
    monkeypatch.setattr(billing_risk_web, "_retention_active_customer_ids", lambda _db: ["12345", "54321"])
    monkeypatch.setattr(
        billing_risk_web,
        "_retention_engagements_by_customer",
        lambda _db, customer_ids: {
            customer_id: [
                {
                    "id": f"engagement-{customer_id}",
                    "customerId": customer_id,
                    "customerName": f"Customer {customer_id}",
                    "outcome": "Promised to Pay",
                    "note": "Follow up",
                    "followUp": "",
                    "rep": "Sales Rep",
                    "repPersonId": "rep-1",
                    "createdAt": "2026-04-14T10:00:00",
                }
            ]
            for customer_id in customer_ids
        },
    )
    monkeypatch.setattr(billing_risk_web, "_retention_saved_only_rows", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        billing_risk_service,
        "get_billing_risk_segment_breakdown",
        lambda _rows: [{"segment": "Suspended", "count": len(_rows), "share_pct": 100.0}],
    )

    captured: dict[str, object] = {}

    def fake_cached_rows_by_external_ids(_db, customer_ids, **kwargs):
        captured["customer_ids"] = list(customer_ids)
        captured["kwargs"] = kwargs
        return [
            {
                "name": "Blocked Customer",
                "_external_id": "12345",
                "subscriber_id": "12345",
                "email": "blocked@example.com",
                "phone": "+2348099991111",
                "city": "Abuja",
                "area": "Maitama",
                "plan": "Home Fiber 50Mbps",
                "subscriber_status": "Suspended",
                "risk_segment": "Suspended",
                "balance": 9200.0,
                "days_past_due": 18,
            }
        ]

    monkeypatch.setattr(billing_risk_cache, "cached_rows_by_external_ids", fake_cached_rows_by_external_ids)
    monkeypatch.setattr(
        billing_risk_cache,
        "all_cached_rows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full cached scan should not be used")),
    )

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/customer-retention",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )
    db = SimpleNamespace(
        query=lambda *_args, **_kwargs: None,
        execute=lambda *_args, **_kwargs: None,
    )
    response = billing_risk_web.customer_retention_tracker(request=request, db=db)

    assert response.status_code == 200
    assert captured["customer_ids"] == ["12345", "54321"]
    assert captured["kwargs"]["limit"] == 6000


def test_customer_retention_tracker_detail_uses_cached_single_customer(monkeypatch):
    monkeypatch.setattr(billing_risk_web, "get_current_user", lambda _request: {"id": "test-user"})
    monkeypatch.setattr(billing_risk_web, "get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(billing_risk_web, "_retention_rep_options", lambda _db: [])
    monkeypatch.setattr(
        billing_risk_web,
        "settings",
        replace(billing_risk_web.settings, customer_retention_route_use_cache=True),
    )
    monkeypatch.setattr(
        billing_risk_web,
        "_retention_engagements_by_customer",
        lambda _db, customer_ids: {"12345": []} if customer_ids == ["12345"] else {},
    )
    monkeypatch.setattr(billing_risk_service, "enrich_billing_risk_rows", lambda rows: rows)

    captured: dict[str, object] = {}

    def fake_cached_row_by_external_id(_db, external_id, **kwargs):
        captured["external_id"] = external_id
        captured["kwargs"] = kwargs
        return {
            "name": "Tracker Customer",
            "_external_id": external_id,
            "subscriber_id": external_id,
            "phone": "+2348099991111",
            "city": "Abuja",
            "area": "Maitama",
            "plan": "Home Fiber 50Mbps",
            "mrr_total": 42000.0,
            "subscriber_status": "Suspended",
            "risk_segment": "Suspended",
            "balance": 9200.0,
        }

    monkeypatch.setattr(billing_risk_cache, "cached_row_by_external_id", fake_cached_row_by_external_id)
    monkeypatch.setattr(
        billing_risk_cache,
        "all_cached_rows",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full cached scan should not be used")),
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
        db=SimpleNamespace(query=lambda *_args, **_kwargs: None),
        due_soon_days=7,
    )

    assert response.status_code == 200
    assert captured["external_id"] == "12345"
    assert captured["kwargs"]["due_soon_days"] == 7
