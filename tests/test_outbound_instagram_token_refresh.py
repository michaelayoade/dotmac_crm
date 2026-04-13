"""Unit tests for the Instagram outbound 401 → token refresh + retry path.

These tests focus on the small helpers added in `outbound.py` rather than the
full `_send_instagram_message` orchestration, which would require fixturing
out the entire conversation/message stack.
"""

from unittest.mock import MagicMock

import httpx
import pytest

from app.services.crm.inbox import outbound

# ── _looks_like_meta_oauth_error ───────────────────────────────────────────


@pytest.mark.parametrize(
    "body,expected",
    [
        (None, False),
        ("", False),
        ('{"error":"random"}', False),
        # No longer false-positives on the bare phrase "access token"
        ("Missing access token parameter", False),
        ("Session has expired on Sunday, 12-Apr-26", True),
        ('{"error":{"type":"OAuthException","code":190}}', True),
        ("The access token has expired", True),
        ('{"message":"Access token is invalid","code":190}', True),
    ],
)
def test_looks_like_meta_oauth_error(body, expected):
    assert outbound._looks_like_meta_oauth_error(body) is expected


# ── _try_instagram_token_refresh_and_resend ────────────────────────────────


def _make_http_status_error(status_code: int, text: str = "boom") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/")
    response = httpx.Response(status_code, text=text, request=request)
    return httpx.HTTPStatusError("err", request=request, response=response)


def test_refresh_and_resend_returns_none_when_no_token(monkeypatch):
    db = MagicMock()
    monkeypatch.setattr("app.services.meta_messaging.get_token_for_channel", lambda *a, **kw: None)
    result, text, status = outbound._try_instagram_token_refresh_and_resend(
        db, target=None, account_id="me", resend_callable=lambda: {"message_id": "x"}
    )
    assert result is None and text is None and status is None


def test_refresh_failure_marks_token_for_reauth(monkeypatch):
    db = MagicMock()
    fake_token = MagicMock()
    fake_token.refresh_error = None
    monkeypatch.setattr("app.services.meta_messaging.get_token_for_channel", lambda *a, **kw: fake_token)

    def _boom_refresh(_db, _token):
        raise _make_http_status_error(401, "OAuthException expired")

    monkeypatch.setattr("app.services.meta_oauth.refresh_token_sync", _boom_refresh)

    result, _text, status = outbound._try_instagram_token_refresh_and_resend(
        db, target=None, account_id="me", resend_callable=lambda: {"message_id": "x"}
    )
    assert result is None
    assert status == 401
    assert fake_token.refresh_error.startswith("reauth_required:")
    db.commit.assert_called()


def test_refresh_then_successful_resend_returns_result(monkeypatch):
    db = MagicMock()
    fake_token = MagicMock()
    fake_token.refresh_error = "stale"
    monkeypatch.setattr("app.services.meta_messaging.get_token_for_channel", lambda *a, **kw: fake_token)

    def _ok_refresh(_db, _token):
        return _token

    monkeypatch.setattr("app.services.meta_oauth.refresh_token_sync", _ok_refresh)

    expected = {"message_id": "msg-123", "recipient_id": "rec-1"}
    result, text, status = outbound._try_instagram_token_refresh_and_resend(
        db, target=None, account_id="me", resend_callable=lambda: expected
    )
    assert result == expected
    assert text is None and status is None


def test_refresh_succeeds_but_resend_still_fails(monkeypatch):
    db = MagicMock()
    fake_token = MagicMock()
    monkeypatch.setattr("app.services.meta_messaging.get_token_for_channel", lambda *a, **kw: fake_token)
    monkeypatch.setattr("app.services.meta_oauth.refresh_token_sync", lambda _db, _t: _t)

    def _resend():
        raise _make_http_status_error(500, "still broken")

    result, text, status = outbound._try_instagram_token_refresh_and_resend(
        db, target=None, account_id="me", resend_callable=_resend
    )
    assert result is None
    assert status == 500
    assert text == "still broken"
