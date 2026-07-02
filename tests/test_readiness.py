"""Readiness probe reports degraded (503) when a critical dependency is down."""

from app.services import readiness


def test_readiness_ready_when_all_up(monkeypatch):
    monkeypatch.setattr(readiness, "_check_db", lambda: (True, "ok"))
    monkeypatch.setattr(readiness, "_check_redis", lambda: (True, "ok"))
    payload, ready = readiness.readiness_report()
    assert ready is True
    assert payload["status"] == "ready"
    assert payload["checks"]["database"]["ok"] is True
    assert payload["checks"]["redis"]["ok"] is True


def test_readiness_degraded_when_redis_down(monkeypatch):
    monkeypatch.setattr(readiness, "_check_db", lambda: (True, "ok"))
    monkeypatch.setattr(readiness, "_check_redis", lambda: (False, "ConnectionError"))
    payload, ready = readiness.readiness_report()
    assert ready is False
    assert payload["status"] == "degraded"
    assert payload["checks"]["redis"]["ok"] is False


def test_readiness_degraded_when_db_down(monkeypatch):
    monkeypatch.setattr(readiness, "_check_db", lambda: (False, "OperationalError"))
    monkeypatch.setattr(readiness, "_check_redis", lambda: (True, "ok"))
    _, ready = readiness.readiness_report()
    assert ready is False
