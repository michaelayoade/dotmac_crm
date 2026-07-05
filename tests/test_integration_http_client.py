"""Unit tests for the shared IntegrationHttpClient retry/transport engine (A2)."""

from __future__ import annotations

import httpx
import pytest

from app.services.integration import IntegrationHttpClient


class _Rate(Exception):
    def __init__(self, retry_after=None):
        super().__init__("rate limited")
        self.retry_after = retry_after


class _Transient(Exception):
    pass


class _Fatal(Exception):
    pass


class _Client:
    """Fake httpx client: request() returns the next scripted marker; the
    response_handler turns a marker into a return value or a raise."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def request(self, **_kw):
        marker = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        return marker


def _handler(marker, **_kw):
    if isinstance(marker, BaseException):
        raise marker
    return marker


def _client(script, **overrides):
    fake = _Client(script)
    opts = dict(
        client_factory=lambda: fake,
        response_handler=_handler,
        backoff=lambda _a: 0,
        max_attempts=3,
        rate_limit_exc=_Rate,
        retryable_excs=(_Transient,),
        non_retryable_excs=(_Fatal,),
    )
    opts.update(overrides)
    return IntegrationHttpClient(**opts), fake


def test_returns_on_success():
    c, fake = _client([{"ok": True}])
    assert c.request("GET", "/x") == {"ok": True}
    assert fake.calls == 1


def test_retries_retryable_then_succeeds():
    c, fake = _client([_Transient(), _Transient(), {"ok": True}])
    assert c.request("GET", "/x") == {"ok": True}
    assert fake.calls == 3


def test_retryable_exhausts_and_reraises():
    c, fake = _client([_Transient(), _Transient(), _Transient()])
    with pytest.raises(_Transient):
        c.request("GET", "/x")
    assert fake.calls == 3  # max_attempts


def test_non_retryable_raises_immediately():
    c, fake = _client([_Fatal(), {"ok": True}])
    with pytest.raises(_Fatal):
        c.request("GET", "/x")
    assert fake.calls == 1  # no retry


def test_rate_limit_honours_retry_after(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr("app.services.integration.http_client.time.sleep", lambda s: slept.append(s))
    c, _ = _client([_Rate(retry_after=7), {"ok": True}])
    assert c.request("GET", "/x") == {"ok": True}
    assert slept[0] == 7  # retry_after overrode the backoff


def test_transport_error_retries_then_wraps():
    err = httpx.ConnectError("refused")
    c, fake = _client(
        [err, err, err],
        transport_exhausted_factory=lambda e, n: _Fatal(f"gave up after {n}: {e}"),
    )
    with pytest.raises(_Fatal) as exc:
        c.request("GET", "/x")
    assert fake.calls == 3
    assert "gave up after 2" in str(exc.value)


def test_unexpected_exception_wrapped():
    c, _ = _client(
        [ValueError("weird")],
        unexpected_error_factory=lambda e: _Fatal(f"unexpected: {e}"),
    )
    with pytest.raises(_Fatal):
        c.request("GET", "/x")


class _Circuit:
    def __init__(self, open_=False):
        self._open = open_
        self.trips = 0
        self.resets = 0

    def is_open(self):
        return self._open

    def trip(self):
        self.trips += 1
        self._open = True

    def reset(self):
        self.resets += 1
        self._open = False


def test_circuit_fast_fails_when_open():
    circ = _Circuit(open_=True)
    c, fake = _client([{"ok": True}], circuit=circ)
    with pytest.raises(RuntimeError):
        c.request("GET", "/x")
    assert fake.calls == 0  # never hit the wire


def test_circuit_trips_on_transport_and_resets_on_success():
    circ = _Circuit()
    c, _ = _client(
        [httpx.ConnectError("x"), {"ok": True}],
        circuit=circ,
    )
    assert c.request("GET", "/x") == {"ok": True}
    assert circ.trips == 1  # tripped on the transport failure
    assert circ.resets >= 1  # reset when the retry got a response


def test_idempotency_key_header_injected():
    captured: dict = {}

    class _CapClient:
        def request(self, **kw):
            captured.update(kw)
            return {"ok": True}

    c = IntegrationHttpClient(
        client_factory=lambda: _CapClient(),
        response_handler=_handler,
        backoff=lambda _a: 0,
        max_attempts=1,
        auth_headers={"X-API-Key": "k"},
    )
    c.request("POST", "/x", idempotency_key="idem-1")
    assert captured["headers"]["Idempotency-Key"] == "idem-1"
    assert captured["headers"]["X-API-Key"] == "k"
