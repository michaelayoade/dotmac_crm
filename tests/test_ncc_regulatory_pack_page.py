"""Render tests for the NCC regulatory-pack admin page template.

The route (``ncc_regulatory_pack_page``) is a thin wrapper over the already
tested ``build_regulatory_pack`` service; the part that regresses is the
template. These render it with representative available/degraded packs (the
parent layout stubbed) so both branches — full data and graceful-degradation
notices — stay working.
"""

from __future__ import annotations

import pytest
from jinja2 import ChoiceLoader, DictLoader

from app.web.templates import Jinja2Templates

_TEMPLATE = "admin/reports/ncc_regulatory_pack.html"


@pytest.fixture()
def render_pack():
    templates = Jinja2Templates(directory="templates")
    env = templates.env
    # Stub the parent layout so the child's {% block content %} renders alone.
    env.loader = ChoiceLoader([DictLoader({"layouts/admin.html": "{% block content %}{% endblock %}"}), env.loader])
    tmpl = env.get_template(_TEMPLATE)

    def _render(pack):
        return tmpl.render(
            request=None,
            user=None,
            current_user=None,
            sidebar_stats={},
            pack=pack,
            window_start="2026-04-01",
            window_end="2026-06-30",
            as_of_value="2026-06-30",
            year_value=2026,
            export_url="/admin/reports/ncc/regulatory-pack?x=1",
        )

    return _render


def _full_pack() -> dict:
    return {
        "meta": {
            "sources": {"complaints": True, "subscribers": True, "financials": True, "staff": True},
            "complete": True,
        },
        "complaints": {
            "available": True,
            "total_complaints": 3,
            "by_category": {"Billing": 2, "Quality of Service (Data)": 1},
            "by_status": {"Open": 1, "Resolved": 2},
            "resolved_within_sla": 1,
            "resolved_total": 2,
        },
        "subscribers": {
            "available": True,
            "report": {
                "total_active_subscriptions": 42,
                "by_connection": {"wired": 30, "wireless": 12},
                "by_customer_type": {"individual": 40, "corporate": 2},
                "by_speed_band": {"10Mbps+": 42},
                "by_region": {"South West": 42},
                "by_state": {"Lagos": 42},
                "network_capacity": {"points_of_presence": 28, "data_usage_tb": "2760.96"},
            },
        },
        "financials": {
            "available": True,
            "financials": {
                "summary": {
                    "total_revenue": "N100",
                    "total_operating_expenses": "N80",
                    "net_income": "N20",
                    "total_assets": "N500",
                    "total_liabilities": "N300",
                    "total_equity": "N200",
                    "is_balanced": True,
                },
                "note": "erp chart-of-accounts basis",
            },
        },
        "staff": {
            "available": True,
            "staff": {
                "total_active": 4,
                "by_category": {
                    "MANAGERIAL": {
                        "nigerian": {"male": 1, "female": 1, "other": 0},
                        "expatriate": {"male": 0, "female": 0, "other": 0},
                        "unknown": {"male": 0, "female": 0, "other": 0},
                    }
                },
            },
        },
    }


def _degraded_pack() -> dict:
    return {
        "meta": {
            "sources": {"complaints": True, "subscribers": False, "financials": False, "staff": False},
            "complete": False,
        },
        "complaints": {
            "available": True,
            "total_complaints": 1,
            "by_category": {"Billing": 1},
            "by_status": {"Open": 1},
            "resolved_within_sla": 0,
            "resolved_total": 0,
        },
        "subscribers": {"available": False, "error": "sub unreachable"},
        "financials": {"available": False, "error": "dotmac_erp is not configured"},
        "staff": {"available": False, "error": "dotmac_erp is not configured"},
    }


def test_page_renders_all_sections_when_available(render_pack):
    html = render_pack(_full_pack())
    assert "① Quarterly Complaints" in html
    assert "② Quarterly Subscriber" in html
    assert "Section F" in html and "Section G" in html
    # section data surfaces
    assert "42" in html  # active subscriptions
    assert "Lagos" in html
    assert "N100" in html  # revenue
    assert "MANAGERIAL" in html.upper()
    assert "Download JSON" in html
    # all sources available → no "Unavailable" pill text
    assert "Unavailable" not in html


def test_page_renders_degradation_notices(render_pack):
    html = render_pack(_degraded_pack())
    # ① still renders, upstream errors surface verbatim
    assert "① Quarterly Complaints" in html
    assert "sub unreachable" in html
    assert "dotmac_erp is not configured" in html
    assert "Unavailable" in html


def test_pack_page_route_is_registered():
    from app.web.admin.reports import router

    paths = {route.path for route in router.routes}
    assert "/reports/ncc/pack" in paths
