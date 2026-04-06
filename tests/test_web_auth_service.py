import logging

from fastapi import HTTPException
from starlette.requests import Request

from app.services import web_auth


def _make_request():
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/auth/login",
        "headers": [],
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


def test_login_submit_redirects_to_mfa_when_mfa_required(db_session, monkeypatch):
    request = _make_request()

    monkeypatch.setattr(
        web_auth.auth_flow_service.auth_flow,
        "login",
        lambda **kwargs: {"mfa_required": True, "mfa_token": "mfa-token"},
    )

    response = web_auth.login_submit(
        request=request,
        db=db_session,
        username="user@example.com",
        password="secret",
        remember=False,
        next_url="/admin/dashboard",
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/mfa?next=/admin/dashboard"
    assert "mfa_pending=mfa-token" in response.headers.get("set-cookie", "")


def test_refresh_invalid_token_logs_at_info(db_session, monkeypatch, caplog):
    request = _make_request()

    monkeypatch.setattr(web_auth.AuthFlow, "resolve_refresh_token", lambda request, token, db: "refresh-token")

    def _raise_invalid_refresh(db, refresh_token, request):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    monkeypatch.setattr(web_auth.auth_flow_service.auth_flow, "refresh", _raise_invalid_refresh)

    with caplog.at_level(logging.INFO, logger="app.services.web_auth"):
        response = web_auth.refresh(request=request, db=db_session, next_url="/admin/crm/inbox")

    assert response.status_code == 303
    assert "web_refresh_redirect_login reason=refresh_failed" in caplog.text
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]
