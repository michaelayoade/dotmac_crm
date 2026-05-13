from contextlib import suppress

from starlette.requests import Request

from app import db as db_module
from app.config import Settings


class _DummySession:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _request(path: str) -> Request:
    return Request({"type": "http", "method": "GET", "path": path, "headers": []})


def test_get_db_reuses_request_session_when_enabled(monkeypatch):
    monkeypatch.setenv("REQUEST_SHARED_DB_SESSION_ENABLED", "1")
    monkeypatch.setenv("REQUEST_SHARED_DB_SESSION_PATH_PREFIXES", "/admin/crm,/admin/dashboard")
    monkeypatch.setattr(db_module, "settings", Settings())
    shared = _DummySession()
    request = _request("/admin/crm/inbox")
    request.state.middleware_db = shared

    dependency = db_module.get_request_db_session(request)

    assert next(dependency) is shared
    with suppress(StopIteration):
        next(dependency)

    assert shared.closed is False


def test_get_db_falls_back_to_local_session_for_unmatched_path(monkeypatch):
    monkeypatch.setenv("REQUEST_SHARED_DB_SESSION_ENABLED", "1")
    monkeypatch.setenv("REQUEST_SHARED_DB_SESSION_PATH_PREFIXES", "/admin/crm")
    monkeypatch.setattr(db_module, "settings", Settings())
    local = _DummySession()
    monkeypatch.setattr(db_module, "SessionLocal", lambda: local)
    request = _request("/admin/system")
    request.state.middleware_db = _DummySession()

    dependency = db_module.get_request_db_session(request)

    assert next(dependency) is local
    with suppress(StopIteration):
        next(dependency)

    assert local.closed is True


def test_end_read_only_transaction_rolls_back_active_transaction():
    class _TransactionalSession:
        def __init__(self):
            self.rolled_back = False

        def in_transaction(self):
            return True

        def rollback(self):
            self.rolled_back = True

    session = _TransactionalSession()

    db_module.end_read_only_transaction(session)

    assert session.rolled_back is True
