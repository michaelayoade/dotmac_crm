"""Tests for the CSRF dependency guarding the cookie-authenticated avatar APIs.

See ``enforce_csrf_for_cookie_auth`` (app/csrf.py), applied to
``POST/DELETE /auth/me/avatar`` which the global middleware skips (``/auth/*``).
"""

import pytest

from app.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    CSRFValidationError,
    enforce_csrf_for_cookie_auth,
)


class _Req:
    def __init__(self, headers=None, cookies=None):
        self.headers = headers or {}
        self.cookies = cookies or {}


def test_bearer_auth_is_exempt_from_csrf():
    req = _Req(headers={"authorization": "Bearer abc.def.ghi"})
    # Token-based auth is not susceptible to CSRF: no exception.
    assert enforce_csrf_for_cookie_auth(req) is None


def test_cookie_auth_with_matching_token_passes():
    req = _Req(
        headers={CSRF_HEADER_NAME: "tok-123"},
        cookies={CSRF_COOKIE_NAME: "tok-123"},
    )
    assert enforce_csrf_for_cookie_auth(req) is None


def test_cookie_auth_without_token_is_rejected():
    req = _Req(cookies={"session_token": "sess"})
    with pytest.raises(CSRFValidationError) as exc:
        enforce_csrf_for_cookie_auth(req)
    assert exc.value.status_code == 403


def test_cookie_auth_with_mismatched_token_is_rejected():
    req = _Req(
        headers={CSRF_HEADER_NAME: "header-token"},
        cookies={CSRF_COOKIE_NAME: "cookie-token"},
    )
    with pytest.raises(CSRFValidationError):
        enforce_csrf_for_cookie_auth(req)
