"""Tests for the NCC regulatory-pack aggregator.

Verify the pack composes ① complaints (native), ② subscribers (dotmac_sub) and
③ financials + staff (dotmac_erp), and that external sections degrade
gracefully when sub/erp is unreachable or unconfigured.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.services import ncc_regulatory_pack as pack

_START = datetime(2026, 4, 1, tzinfo=UTC)
_END = datetime(2026, 6, 30, 23, 59, 59, tzinfo=UTC)


class _FakeERPClient:
    def __init__(self, *, financials=None, staff=None, boom=False):
        self._financials = financials or {}
        self._staff = staff or {}
        self._boom = boom

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get_ncc_financials(self, **kwargs):
        if self._boom:
            raise RuntimeError("erp down")
        return self._financials

    def get_ncc_staff_headcount(self):
        if self._boom:
            raise RuntimeError("erp down")
        return self._staff


# ── ① complaints section ────────────────────────────────────────────────────
def test_complaints_section_summarizes_records(monkeypatch, db_session):
    records = [
        {"Category": "Billing", "Status": "Resolved", "Resolved within SLA": "Yes"},
        {"Category": "Billing", "Status": "Resolved", "Resolved within SLA": "No"},
        {"Category": "Quality of Service (Data)", "Status": "Open", "Resolved within SLA": ""},
    ]
    monkeypatch.setattr("app.web.admin.reports._build_ncc_records", lambda db, s, e: records)

    section = pack.complaints_section(db_session, _START, _END)

    assert section["available"] is True
    assert section["total_complaints"] == 3
    assert section["by_category"]["Billing"] == 2
    assert section["by_status"] == {"Open": 1, "Resolved": 2}
    assert section["resolved_within_sla"] == 1
    assert section["resolved_total"] == 2


# ── ② subscribers section ───────────────────────────────────────────────────
def test_subscribers_section_available(monkeypatch, db_session):
    monkeypatch.setattr(
        "app.services.selfcare.fetch_ncc_subscriber_report",
        lambda db, **kw: {"total_active_subscriptions": 42},
    )
    section = pack.subscribers_section(db_session, as_of="2026-06-30")
    assert section["available"] is True
    assert section["report"]["total_active_subscriptions"] == 42


def test_subscribers_section_applies_pack_state_adjustments(monkeypatch, db_session):
    monkeypatch.setattr(
        "app.services.selfcare.fetch_ncc_subscriber_report",
        lambda db, **kw: {
            "total_active_subscriptions": 2868,
            "by_state": {
                "Anambra": 1,
                "Federal Capital Territory": 2414,
                "Lagos": 114,
                "Oyo": 5,
                "Unknown": 334,
            },
            "by_region": {
                "North Central": 2414,
                "South East": 1,
                "South West": 119,
                "Unknown": 334,
            },
            "subscription_matrix": {
                "corporate": {"wired": 10, "wireless": 20},
                "individual": {"wired": 1000, "wireless": 1838},
            },
            "network_capacity": {
                "points_of_presence": 36,
                "points_of_presence_source": "active_pop_sites",
            },
        },
    )

    section = pack.subscribers_section(db_session, as_of="2026-07-07")

    report = section["report"]
    assert section["available"] is True
    assert report["by_state"] == {"Abuja": 2748, "Lagos": 114}
    assert report["by_region"] == {"North Central": 2748, "South West": 114}
    assert report["total_active_subscriptions"] == 2862
    assert sum(report["by_state"].values()) == report["total_active_subscriptions"]
    assert sum(report["by_region"].values()) == report["total_active_subscriptions"]
    assert (
        report["subscription_matrix"]["corporate"]["wired"]
        + report["subscription_matrix"]["corporate"]["wireless"]
        + report["subscription_matrix"]["individual"]["wired"]
        + report["subscription_matrix"]["individual"]["wireless"]
    ) == report["total_active_subscriptions"]
    assert report["network_capacity"]["points_of_presence"] == 30
    assert report["ncc_pack_adjustments"]["excluded_count"] == 6


def test_subscribers_section_degrades_on_error(monkeypatch, db_session):
    def _boom(db, **kw):
        raise RuntimeError("sub unreachable")

    monkeypatch.setattr("app.services.selfcare.fetch_ncc_subscriber_report", _boom)
    section = pack.subscribers_section(db_session, as_of="2026-06-30")
    assert section["available"] is False
    assert "sub unreachable" in section["error"]


def test_subscribers_section_degrades_on_empty(monkeypatch, db_session):
    monkeypatch.setattr("app.services.selfcare.fetch_ncc_subscriber_report", lambda db, **kw: {})
    section = pack.subscribers_section(db_session, as_of="2026-06-30")
    assert section["available"] is False


# ── ③ financials + staff sections ───────────────────────────────────────────
def test_financials_section_unconfigured(monkeypatch, db_session):
    monkeypatch.setattr(pack, "_build_erp_client", lambda db: None)
    section = pack.financials_section(db_session, year=2026)
    assert section["available"] is False
    assert "not configured" in section["error"]


def test_financials_section_available(monkeypatch, db_session):
    monkeypatch.setattr(
        pack,
        "_build_erp_client",
        lambda db: _FakeERPClient(financials={"summary": {"total_revenue": "N1"}}),
    )
    section = pack.financials_section(db_session, year=2026)
    assert section["available"] is True
    assert section["financials"]["summary"]["total_revenue"] == "N1"


def test_staff_section_degrades_on_error(monkeypatch, db_session):
    monkeypatch.setattr(pack, "_build_erp_client", lambda db: _FakeERPClient(boom=True))
    section = pack.staff_section(db_session)
    assert section["available"] is False
    assert "erp down" in section["error"]


def test_staff_section_uses_fallback_for_unclassified_erp_headcount(monkeypatch, db_session):
    erp_staff = {
        "total_active": 67,
        "by_category": {
            "OTHER": {
                "unknown": {"male": 0, "female": 0, "other": 67},
            },
        },
    }
    monkeypatch.setattr(pack, "_build_erp_client", lambda db: _FakeERPClient(staff=erp_staff))

    section = pack.staff_section(db_session)

    assert section["available"] is True
    assert section["staff"]["total_active"] == 170
    assert section["staff"]["by_category"]["MANAGERIAL"]["nigerian"] == {
        "male": 14,
        "female": 5,
        "other": 3,
    }
    assert section["staff"]["by_category"]["SENIOR_TECHNICAL"]["nigerian"]["male"] == 32
    assert section["staff"]["by_category"]["JUNIOR_TECHNICAL"]["nigerian"]["other"] == 29
    assert section["staff"]["by_category"]["OTHER"]["nigerian"]["female"] == 14


def test_staff_section_keeps_classified_erp_headcount(monkeypatch, db_session):
    erp_staff = {
        "total_active": 5,
        "by_category": {
            "MANAGERIAL": {
                "nigerian": {"male": 1, "female": 2, "other": 0},
            },
        },
    }
    monkeypatch.setattr(pack, "_build_erp_client", lambda db: _FakeERPClient(staff=erp_staff))

    section = pack.staff_section(db_session)

    assert section["available"] is True
    assert section["staff"] == erp_staff


# ── the whole pack ──────────────────────────────────────────────────────────
def test_pack_complete_when_all_sources_available(monkeypatch, db_session):
    monkeypatch.setattr(
        "app.web.admin.reports._build_ncc_records",
        lambda db, s, e: [{"Category": "Billing", "Status": "Open"}],
    )
    monkeypatch.setattr(
        "app.services.selfcare.fetch_ncc_subscriber_report",
        lambda db, **kw: {"total_active_subscriptions": 10},
    )
    monkeypatch.setattr(
        pack,
        "_build_erp_client",
        lambda db: _FakeERPClient(
            financials={"summary": {}},
            staff={
                "total_active": 5,
                "by_category": {
                    "MANAGERIAL": {
                        "nigerian": {"male": 5, "female": 0, "other": 0},
                    },
                },
            },
        ),
    )

    result = pack.build_regulatory_pack(db_session, start_dt=_START, end_dt=_END, as_of="2026-06-30", year=2026)

    assert result["meta"]["complete"] is True
    assert result["meta"]["sources"] == {
        "complaints": True,
        "subscribers": True,
        "financials": True,
        "staff": True,
    }
    assert result["meta"]["year"] == 2026
    assert result["complaints"]["total_complaints"] == 1
    assert result["subscribers"]["report"]["total_active_subscriptions"] == 10
    assert result["staff"]["staff"]["total_active"] == 5


def test_pack_degrades_but_returns_when_upstreams_down(monkeypatch, db_session):
    monkeypatch.setattr(
        "app.web.admin.reports._build_ncc_records",
        lambda db, s, e: [{"Category": "Billing", "Status": "Open"}],
    )

    def _boom(db, **kw):
        raise RuntimeError("sub down")

    monkeypatch.setattr("app.services.selfcare.fetch_ncc_subscriber_report", _boom)
    monkeypatch.setattr(pack, "_build_erp_client", lambda db: None)

    result = pack.build_regulatory_pack(db_session, start_dt=_START, end_dt=_END, as_of="2026-06-30", year=2026)

    # ① native return still renders; the pack reports which upstreams are missing.
    assert result["complaints"]["available"] is True
    assert result["meta"]["complete"] is False
    assert result["meta"]["sources"]["complaints"] is True
    assert result["meta"]["sources"]["subscribers"] is False
    assert result["meta"]["sources"]["financials"] is False
    assert result["meta"]["sources"]["staff"] is False


# ── route wrapper ───────────────────────────────────────────────────────────
def test_regulatory_pack_route_defaults_year_and_serializes(monkeypatch, db_session):
    import json

    from app.web.admin import reports as reports_routes

    monkeypatch.setattr(
        "app.web.admin.reports._build_ncc_records",
        lambda db, s, e: [{"Category": "Billing", "Status": "Open"}],
    )
    monkeypatch.setattr("app.services.selfcare.fetch_ncc_subscriber_report", lambda db, **kw: {})
    monkeypatch.setattr("app.services.ncc_regulatory_pack._build_erp_client", lambda db: None)

    # Pass every param explicitly (calling the route function directly leaves
    # FastAPI Query(...) sentinels in place otherwise).
    response = reports_routes.ncc_regulatory_pack(
        db=db_session,
        start_date="2026-04-01",
        end_date="2026-06-30",
        as_of=None,
        year=None,
        statuses=None,
        reseller_id=None,
        access_capacity_gbps=None,
        unutilized_capacity_mbps=None,
        points_of_presence=None,
        data_usage_tb=None,
    )

    body = json.loads(response.body)
    # year/as_of default from the complaints window end when omitted
    assert body["meta"]["year"] == 2026
    assert body["meta"]["as_of"] == "2026-06-30"
    assert body["complaints"]["available"] is True
    assert body["meta"]["complete"] is False
