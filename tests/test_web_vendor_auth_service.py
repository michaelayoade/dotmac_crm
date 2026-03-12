from types import SimpleNamespace

from starlette.requests import Request

from app.services import web_vendor_auth


def _make_request(path: str = "/vendor/auth/login", cookies: dict[str, str] | None = None):
    headers = []
    if cookies:
        cookie_header = "; ".join(f"{key}={value}" for key, value in cookies.items())
        headers.append((b"cookie", cookie_header.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": headers,
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


def test_vendor_login_submit_redirects_to_mfa_without_secure_cookie_when_disabled(db_session, monkeypatch):
    request = _make_request()
    monkeypatch.setattr(web_vendor_auth, "settings", SimpleNamespace(cookie_secure=False))
    monkeypatch.setattr(
        web_vendor_auth.vendor_portal,
        "login",
        lambda *args, **kwargs: {"mfa_required": True, "mfa_token": "vendor-mfa-token"},
    )

    response = web_vendor_auth.vendor_login_submit(
        request=request,
        db=db_session,
        username="vendor@example.com",
        password="secret",
        remember=False,
    )

    set_cookie = "\n".join(response.headers.getlist("set-cookie"))
    assert response.status_code == 303
    assert response.headers["location"] == "/vendor/auth/mfa"
    assert "vendor_mfa_pending=vendor-mfa-token" in set_cookie
    assert "Secure" not in set_cookie


def test_vendor_mfa_submit_sets_session_cookie_without_secure_flag_when_disabled(db_session, monkeypatch):
    request = _make_request(
        path="/vendor/auth/mfa",
        cookies={"vendor_mfa_pending": "pending-token", "vendor_mfa_remember": "0"},
    )
    monkeypatch.setattr(web_vendor_auth, "settings", SimpleNamespace(cookie_secure=False))
    monkeypatch.setattr(
        web_vendor_auth.vendor_portal,
        "verify_mfa",
        lambda *args, **kwargs: {"session_token": "vendor-session-token"},
    )
    monkeypatch.setattr(web_vendor_auth.vendor_portal, "get_session_max_age", lambda db: 3600)

    response = web_vendor_auth.vendor_mfa_submit(
        request=request,
        db=db_session,
        code="123456",
    )

    set_cookie = "\n".join(response.headers.getlist("set-cookie"))
    assert response.status_code == 303
    assert response.headers["location"] == "/vendor/dashboard"
    assert "vendor_session=vendor-session-token" in set_cookie
    assert "Secure" not in set_cookie
