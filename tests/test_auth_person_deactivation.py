"""Regression tests: deactivated people must lose access immediately.

Covers the cascade added for the field-app security prerequisites:
- login / token issuance blocked for disabled or archived people
- refresh rejected and the session revoked once a person is disabled
- require_user_auth rejects tokens of disabled people
- People.update deactivation revokes all active sessions
- role/permission changes invalidate cached session claims
"""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.models.auth import AuthProvider, SessionStatus, UserCredential
from app.models.auth import Session as AuthSession
from app.models.person import PersonStatus
from app.models.rbac import Role
from app.schemas.person import PersonUpdate
from app.schemas.rbac import PersonRoleCreate
from app.services.auth_dependencies import require_user_auth
from app.services.auth_flow import (
    AuthFlow,
    hash_password,
    person_is_enabled,
    revoke_sessions_for_person,
)
from app.services.person import people
from app.services.rbac import person_roles


def _make_request(user_agent: str = "pytest"):
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/auth",
        "headers": [(b"user-agent", user_agent.encode("utf-8"))],
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


def _add_credential(db_session, person, password: str = "secret"):
    credential = UserCredential(
        person_id=person.id,
        provider=AuthProvider.local,
        username=person.email,
        password_hash=hash_password(password),
        is_active=True,
    )
    db_session.add(credential)
    db_session.commit()
    db_session.refresh(credential)
    return credential


def test_person_is_enabled_helper(person):
    assert person_is_enabled(person) is True
    person.is_active = False
    assert person_is_enabled(person) is False
    person.is_active = True
    person.status = PersonStatus.archived
    assert person_is_enabled(person) is False
    assert person_is_enabled(None) is False


def test_login_blocked_for_deactivated_person(db_session, person):
    _add_credential(db_session, person)
    person.is_active = False
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        AuthFlow.login(db_session, person.email, "secret", _make_request(), None)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Account disabled"


def test_login_blocked_for_archived_person(db_session, person):
    _add_credential(db_session, person)
    person.status = PersonStatus.archived
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        AuthFlow.login(db_session, person.email, "secret", _make_request(), None)
    assert exc.value.status_code == 403


def test_refresh_rejected_and_session_revoked_after_deactivation(db_session, person):
    _add_credential(db_session, person)
    tokens = AuthFlow.login(db_session, person.email, "secret", _make_request(), None)

    person.is_active = False
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        AuthFlow.refresh(db_session, tokens["refresh_token"], _make_request())
    assert exc.value.status_code == 401

    session = db_session.query(AuthSession).filter(AuthSession.person_id == person.id).first()
    assert session.status == SessionStatus.revoked
    assert session.revoked_at is not None


def test_require_user_auth_rejects_deactivated_person(db_session, person, monkeypatch):
    # Force the DB validation path: the Redis cache must miss.
    monkeypatch.setattr("app.services.auth_dependencies.get_cached_session", lambda _sid: None)

    _add_credential(db_session, person)
    tokens = AuthFlow.login(db_session, person.email, "secret", _make_request(), None)

    auth = require_user_auth(request=None, authorization=f"Bearer {tokens['access_token']}", db=db_session)
    assert auth["person_id"] == str(person.id)

    person.is_active = False
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        require_user_auth(request=None, authorization=f"Bearer {tokens['access_token']}", db=db_session)
    assert exc.value.status_code == 401


def test_people_update_deactivation_revokes_sessions(db_session, person, monkeypatch):
    invalidated: list[str] = []
    monkeypatch.setattr("app.services.auth_flow.invalidate_session", invalidated.append)

    _add_credential(db_session, person)
    AuthFlow.login(db_session, person.email, "secret", _make_request(), None)
    session = db_session.query(AuthSession).filter(AuthSession.person_id == person.id).first()
    assert session.status == SessionStatus.active

    people.update(db_session, str(person.id), PersonUpdate(is_active=False))

    db_session.refresh(session)
    assert session.status == SessionStatus.revoked
    assert str(session.id) in invalidated


def test_people_update_without_deactivation_keeps_sessions(db_session, person):
    _add_credential(db_session, person)
    AuthFlow.login(db_session, person.email, "secret", _make_request(), None)

    people.update(db_session, str(person.id), PersonUpdate(first_name="Renamed"))

    session = db_session.query(AuthSession).filter(AuthSession.person_id == person.id).first()
    assert session.status == SessionStatus.active


def test_revoke_sessions_for_person_revokes_all_active(db_session, person):
    _add_credential(db_session, person)
    AuthFlow.login(db_session, person.email, "secret", _make_request(), None)
    AuthFlow.login(db_session, person.email, "secret", _make_request(), None)

    count = revoke_sessions_for_person(db_session, person.id)
    assert count == 2

    active = (
        db_session.query(AuthSession)
        .filter(AuthSession.person_id == person.id)
        .filter(AuthSession.status == SessionStatus.active)
        .count()
    )
    assert active == 0
    # Idempotent: nothing left to revoke.
    assert revoke_sessions_for_person(db_session, person.id) == 0


def test_role_change_invalidates_cached_session_claims(db_session, person, monkeypatch):
    invalidated: list[str] = []
    monkeypatch.setattr("app.services.auth_flow.invalidate_session", invalidated.append)

    _add_credential(db_session, person)
    AuthFlow.login(db_session, person.email, "secret", _make_request(), None)
    session = db_session.query(AuthSession).filter(AuthSession.person_id == person.id).first()

    role = Role(name=f"test-role-{person.id.hex[:8]}", is_active=True)
    db_session.add(role)
    db_session.commit()

    link = person_roles.create(db_session, PersonRoleCreate(person_id=person.id, role_id=role.id))
    assert str(session.id) in invalidated

    invalidated.clear()
    person_roles.delete(db_session, str(link.id))
    assert str(session.id) in invalidated
