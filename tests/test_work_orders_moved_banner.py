"""Tests for the setting-gated work-orders-moved transition banner (Phase 2 flip)."""

from urllib.parse import urlsplit

from starlette.requests import Request

from app.models.domain_settings import SettingDomain
from app.services import settings_spec
from app.web.admin import operations as admin_operations

BANNER_MESSAGE = "Work orders have moved to the sub admin"


def _make_request(path: str = "/admin/operations/work-orders") -> Request:
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


def _patch_auth(monkeypatch):
    monkeypatch.setattr("app.web.admin.operations.get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(
        "app.web.admin.operations.get_current_user",
        lambda _request: {"roles": [], "permissions": []},
    )


def _patch_settings(monkeypatch, overrides):
    real_resolve = settings_spec.resolve_value

    def fake(db, domain, key, **kwargs):
        if (domain, key) in overrides:
            return overrides[(domain, key)]
        return real_resolve(db, domain, key, **kwargs)

    monkeypatch.setattr(settings_spec, "resolve_value", fake)


def _call_work_orders_list(db_session):
    return admin_operations.work_orders_list(
        request=_make_request(),
        db=db_session,
        status=None,
        priority=None,
        assigned=None,
        scheduled=None,
        period_days=30,
        start_date=None,
        end_date=None,
        page=1,
        per_page=20,
    )


def test_work_orders_list_hides_moved_banner_by_default(monkeypatch, db_session, work_order):
    _patch_auth(monkeypatch)

    response = _call_work_orders_list(db_session)

    assert response.context["work_orders_moved_banner"] is None
    assert BANNER_MESSAGE not in response.body.decode()


def test_work_orders_list_shows_moved_banner_when_enabled(monkeypatch, db_session, work_order):
    _patch_auth(monkeypatch)
    _patch_settings(
        monkeypatch,
        {
            (SettingDomain.integration, "work_orders_moved_banner_enabled"): True,
            (
                SettingDomain.integration,
                "work_orders_moved_banner_url",
            ): "https://sub.example.com/admin/dispatch/work-orders",
        },
    )

    response = _call_work_orders_list(db_session)

    assert response.context["work_orders_moved_banner"] == {"url": "https://sub.example.com/admin/dispatch/work-orders"}
    body = response.body.decode()
    assert BANNER_MESSAGE in body
    assert "this system is read-only" in body
    assert "https://sub.example.com/admin/dispatch/work-orders" in body


def test_moved_banner_url_defaults_to_selfcare_base_url(monkeypatch, db_session):
    _patch_settings(
        monkeypatch,
        {
            (SettingDomain.integration, "work_orders_moved_banner_enabled"): True,
            (SettingDomain.integration, "work_orders_moved_banner_url"): None,
            (SettingDomain.integration, "selfcare_base_url"): "https://selfcare.dotmac.io/",
        },
    )

    banner = admin_operations._work_orders_moved_banner(db_session)

    assert banner == {"url": "https://selfcare.dotmac.io/admin/dispatch/work-orders"}


def test_work_order_detail_shows_moved_banner_when_enabled(monkeypatch, db_session, work_order):
    _patch_auth(monkeypatch)
    _patch_settings(
        monkeypatch,
        {
            (SettingDomain.integration, "work_orders_moved_banner_enabled"): True,
            (
                SettingDomain.integration,
                "work_orders_moved_banner_url",
            ): "https://sub.example.com/admin/dispatch/work-orders",
        },
    )

    response = admin_operations.work_order_detail(
        request=_make_request(f"/admin/operations/work-orders/{work_order.id}"),
        order_id=work_order.id,
        db=db_session,
    )

    body = response.body.decode()
    assert BANNER_MESSAGE in body
    assert "https://sub.example.com/admin/dispatch/work-orders" in body


def test_work_order_detail_hides_moved_banner_by_default(monkeypatch, db_session, work_order):
    _patch_auth(monkeypatch)

    response = admin_operations.work_order_detail(
        request=_make_request(f"/admin/operations/work-orders/{work_order.id}"),
        order_id=work_order.id,
        db=db_session,
    )

    assert BANNER_MESSAGE not in response.body.decode()
