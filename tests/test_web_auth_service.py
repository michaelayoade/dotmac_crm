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
