"""S3: crm->sub reachability circuit breaker.

A slow/unreachable sub otherwise makes every per-subscriber selfcare call wait
out the full timeout; CRM fan-out pages turn one outage into N x timeout. The
first connection/timeout failure trips a short breaker so the rest of the window
fast-fails; a successful connection closes it.
"""

from __future__ import annotations

import pytest

from app.services import selfcare
from app.services.selfcare import _REACHABILITY_CIRCUIT, SelfcareProviderError


@pytest.fixture(autouse=True)
def _reset_circuit():
    # The breaker is a module singleton — isolate tests from each other.
    _REACHABILITY_CIRCUIT.reset()
    yield
    _REACHABILITY_CIRCUIT.reset()


def _patch(monkeypatch, handler):
    import httpx

    monkeypatch.setattr(
        selfcare,
        "_get_api_config",
        lambda db: {"base_url": "http://x", "api_token": "t", "timeout_seconds": 5},
    )
    monkeypatch.setattr(selfcare, "_sleep_backoff", lambda attempt: None)
    monkeypatch.setattr(httpx.Client, "request", lambda self, method, url, **kw: handler(method, url, **kw))


class _Resp:
    status_code = 200

    def json(self):
        return {"ok": True}


def test_circuit_trip_and_reset():
    assert not _REACHABILITY_CIRCUIT.is_open()
    _REACHABILITY_CIRCUIT.trip()
    assert _REACHABILITY_CIRCUIT.is_open()
    _REACHABILITY_CIRCUIT.reset()
    assert not _REACHABILITY_CIRCUIT.is_open()


def test_open_circuit_fast_fails_without_a_request(monkeypatch):
    calls = {"n": 0}

    def handler(*_a, **_k):
        calls["n"] += 1
        raise AssertionError("request must not be made while the circuit is open")

    _patch(monkeypatch, handler)
    _REACHABILITY_CIRCUIT.trip()

    with pytest.raises(SelfcareProviderError):
        selfcare._request_json(None, "GET", "/x")
    assert calls["n"] == 0


def test_connection_failure_trips_the_circuit(monkeypatch):
    import httpx

    def handler(*_a, **_k):
        raise httpx.ConnectError("sub down")

    _patch(monkeypatch, handler)
    with pytest.raises(SelfcareProviderError):
        selfcare._request_json(None, "GET", "/x")
    assert _REACHABILITY_CIRCUIT.is_open()


def test_recovery_within_retries_closes_the_circuit(monkeypatch):
    import httpx

    state = {"n": 0}

    def handler(*_a, **_k):
        state["n"] += 1
        if state["n"] == 1:
            raise httpx.ConnectError("blip")  # trips
        return _Resp()  # succeeds -> resets

    _patch(monkeypatch, handler)
    assert selfcare._request_json(None, "GET", "/x") == {"ok": True}
    assert not _REACHABILITY_CIRCUIT.is_open()
