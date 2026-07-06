"""Regression test for scripts/provision_service_api_key.py.

The script displays the provisioned key's metadata (person id, key id, label,
expiry) *after* the ``with SessionLocal()`` block. Because ``SessionLocal``
expires attributes on commit, reading those ORM attributes once the session has
closed raised ``DetachedInstanceError`` — the script committed a key and then
crashed before printing the raw value, leaving an orphaned, unrecoverable key.

These tests reproduce that failure by faking a session whose instances become
detached on ``__exit__``; attribute access after that point raises exactly as
SQLAlchemy would.
"""

from __future__ import annotations

import uuid

import pytest
from scripts import provision_service_api_key as provision
from sqlalchemy.orm.exc import DetachedInstanceError


class _DetachableInstance:
    """Stand-in for an ORM instance that raises once the session has closed."""

    def __init__(self, **values: object) -> None:
        object.__setattr__(self, "_values", values)
        object.__setattr__(self, "_bound", True)

    def _detach(self) -> None:
        object.__setattr__(self, "_bound", False)

    def __getattr__(self, name: str) -> object:
        values = object.__getattribute__(self, "_values")
        if name in values:
            if not object.__getattribute__(self, "_bound"):
                raise DetachedInstanceError(f"{name!r} read after the session closed")
            return values[name]
        raise AttributeError(name)


class _MockQuery:
    def __init__(self, person: _DetachableInstance) -> None:
        self._person = person

    def filter(self, *args: object) -> _MockQuery:
        return self

    def first(self) -> _DetachableInstance:
        return self._person

    def all(self) -> list[_DetachableInstance]:
        return []


class _MockSession:
    """Context manager that detaches its instances on exit, like a real one."""

    def __init__(self, person: _DetachableInstance, api_key: _DetachableInstance) -> None:
        self._person = person
        self._api_key = api_key

    def __enter__(self) -> _MockSession:
        return self

    def __exit__(self, *exc: object) -> bool:
        self._person._detach()
        self._api_key._detach()
        return False

    def query(self, _model: object) -> _MockQuery:
        return _MockQuery(self._person)

    def commit(self) -> None:
        pass


@pytest.fixture
def _patched(monkeypatch: pytest.MonkeyPatch) -> tuple[_DetachableInstance, str]:
    person = _DetachableInstance(id=uuid.uuid4())
    api_key = _DetachableInstance(id=uuid.uuid4(), label="dotmac_sub self-care sync", expires_at=None)
    raw_key = "raw-key-shown-once"

    class _FakePerson:
        email = None  # only used in a filter expression the mock query ignores

    monkeypatch.setattr(provision, "SessionLocal", lambda: _MockSession(person, api_key))
    monkeypatch.setattr(provision, "Person", _FakePerson)

    class _FakeApiKeys:
        @staticmethod
        def generate(_db: object, _payload: object) -> tuple[_DetachableInstance, str]:
            return api_key, raw_key

    monkeypatch.setattr(provision, "ApiKeys", _FakeApiKeys)
    return person, raw_key


def test_main_prints_key_without_detached_instance_error(
    _patched: tuple[_DetachableInstance, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """main() must read all displayed attributes before the session closes."""
    _person, raw_key = _patched

    rc = provision.main([])

    assert rc == 0
    out = capsys.readouterr().out
    # The raw key is the whole point — it must reach stdout.
    assert raw_key in out
    assert "Service ApiKey provisioned." in out


def test_metadata_is_captured_while_bound(
    _patched: tuple[_DetachableInstance, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The displayed person/key ids are the real values, not a crash."""
    person, _raw_key = _patched
    expected_id = str(person.id)  # read while still bound, before main() detaches

    provision.main([])

    out = capsys.readouterr().out
    assert expected_id in out
