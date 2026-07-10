"""Tests for the setting-gated tickets-moved transition banner (Phase 1 flip)."""

from urllib.parse import urlsplit

from starlette.requests import Request

from app.models.domain_settings import SettingDomain
from app.models.tickets import TicketStatus
from app.schemas.tickets import TicketCreate
from app.services import settings_spec
from app.services import tickets as tickets_service
from app.web.admin import tickets as admin_tickets

BANNER_MESSAGE = "Tickets have moved to the sub admin"


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


def _patch_auth(monkeypatch):
    monkeypatch.setattr("app.web.admin._auth_helpers.get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr("app.web.admin._auth_helpers.get_current_user", lambda _request: None)


def _patch_settings(monkeypatch, overrides):
    real_resolve = settings_spec.resolve_value

    def fake(db, domain, key, **kwargs):
        if (domain, key) in overrides:
            return overrides[(domain, key)]
        return real_resolve(db, domain, key, **kwargs)

    monkeypatch.setattr(settings_spec, "resolve_value", fake)


def test_tickets_list_hides_moved_banner_by_default(monkeypatch, db_session):
    tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Visible ticket", status=TicketStatus.open),
    )
    _patch_auth(monkeypatch)

    response = admin_tickets.tickets_list(
        request=_make_request(),
        db=db_session,
    )

    assert response.context["tickets_moved_banner"] is None
    assert BANNER_MESSAGE not in response.body.decode()


def test_tickets_list_shows_moved_banner_when_enabled(monkeypatch, db_session):
    tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Visible ticket", status=TicketStatus.open),
    )
    _patch_auth(monkeypatch)
    _patch_settings(
        monkeypatch,
        {
            (SettingDomain.integration, "support_tickets_moved_banner_enabled"): True,
            (
                SettingDomain.integration,
                "support_tickets_moved_banner_url",
            ): "https://sub.example.com/admin/support/tickets",
        },
    )

    response = admin_tickets.tickets_list(
        request=_make_request(),
        db=db_session,
    )

    assert response.context["tickets_moved_banner"] == {"url": "https://sub.example.com/admin/support/tickets"}
    body = response.body.decode()
    assert BANNER_MESSAGE in body
    assert "this system is read-only" in body
    assert "https://sub.example.com/admin/support/tickets" in body


def test_moved_banner_url_defaults_to_selfcare_base_url(monkeypatch, db_session):
    _patch_settings(
        monkeypatch,
        {
            (SettingDomain.integration, "support_tickets_moved_banner_enabled"): True,
            (SettingDomain.integration, "support_tickets_moved_banner_url"): None,
            (SettingDomain.integration, "selfcare_base_url"): "https://selfcare.dotmac.io/",
        },
    )

    banner = admin_tickets._tickets_moved_banner(db_session)

    assert banner == {"url": "https://selfcare.dotmac.io/admin/support/tickets"}


def test_ticket_detail_shows_moved_banner_when_enabled(monkeypatch, db_session):
    ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Detail ticket", status=TicketStatus.open),
    )
    _patch_auth(monkeypatch)
    _patch_settings(
        monkeypatch,
        {
            (SettingDomain.integration, "support_tickets_moved_banner_enabled"): True,
            (
                SettingDomain.integration,
                "support_tickets_moved_banner_url",
            ): "https://sub.example.com/admin/support/tickets",
        },
    )
    ticket_ref = ticket.number or str(ticket.id)

    response = admin_tickets.ticket_detail(
        request=_make_request(f"/admin/support/tickets/{ticket_ref}"),
        ticket_ref=ticket_ref,
        db=db_session,
    )

    body = response.body.decode()
    assert BANNER_MESSAGE in body
    assert "https://sub.example.com/admin/support/tickets" in body


def test_ticket_detail_hides_moved_banner_by_default(monkeypatch, db_session):
    ticket = tickets_service.tickets.create(
        db_session,
        TicketCreate(title="Detail ticket", status=TicketStatus.open),
    )
    _patch_auth(monkeypatch)
    ticket_ref = ticket.number or str(ticket.id)

    response = admin_tickets.ticket_detail(
        request=_make_request(f"/admin/support/tickets/{ticket_ref}"),
        ticket_ref=ticket_ref,
        db=db_session,
    )

    assert BANNER_MESSAGE not in response.body.decode()
