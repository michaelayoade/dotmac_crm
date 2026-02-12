from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.models.auth import Session as AuthSession
from app.models.auth import SessionStatus
from app.models.person import PersonStatus
from app.models.vendor import Vendor, VendorUser
from app.services import vendor_portal


@pytest.fixture(autouse=True)
def _clear_vendor_sessions():
    # vendor_portal uses a simple in-memory session dict.
    vendor_portal._VENDOR_SESSIONS.clear()
    yield
    vendor_portal._VENDOR_SESSIONS.clear()


def test_get_context_inactive_vendor_returns_none(db_session, person):
    vendor = Vendor(name="Inactive Vendor", is_active=False)
    db_session.add(vendor)
    db_session.commit()
    db_session.refresh(vendor)

    link = VendorUser(vendor_id=vendor.id, person_id=person.id, is_active=True)
    db_session.add(link)
    db_session.commit()

    token = vendor_portal._create_session(
        username=person.email,
        person_id=str(person.id),
        vendor_id=str(vendor.id),
        role="vendors",
        remember=False,
        db=db_session,
    )
    assert vendor_portal.get_context(db_session, token) is None


def test_session_from_access_token_inactive_vendor_forbidden(db_session, person, monkeypatch):
    vendor = Vendor(name="Inactive Vendor", is_active=False)
    db_session.add(vendor)
    db_session.commit()
    db_session.refresh(vendor)

    link = VendorUser(vendor_id=vendor.id, person_id=person.id, is_active=True)
    db_session.add(link)

    auth_session = AuthSession(
        person_id=person.id,
        status=SessionStatus.active,
        token_hash="test",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(auth_session)
    db_session.commit()
    db_session.refresh(auth_session)

    monkeypatch.setattr(
        vendor_portal.auth_flow_service,
        "decode_access_token",
        lambda _db, _token: {"sub": str(person.id), "session_id": str(auth_session.id)},
    )

    with pytest.raises(HTTPException) as excinfo:
        vendor_portal._session_from_access_token(db_session, "fake-access-token", username=person.email, remember=False)
    assert excinfo.value.status_code == 403


def test_get_context_inactive_person_returns_none(db_session, person):
    vendor = Vendor(name="Active Vendor", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    db_session.refresh(vendor)

    link = VendorUser(vendor_id=vendor.id, person_id=person.id, is_active=True)
    db_session.add(link)
    db_session.commit()

    person.is_active = False
    person.status = PersonStatus.inactive
    db_session.add(person)
    db_session.commit()

    token = vendor_portal._create_session(
        username=person.email,
        person_id=str(person.id),
        vendor_id=str(vendor.id),
        role="vendors",
        remember=False,
        db=db_session,
    )

    assert vendor_portal.get_context(db_session, token) is None
    # get_context should also invalidate the in-memory session.
    assert token not in vendor_portal._VENDOR_SESSIONS


def test_session_from_access_token_inactive_person_forbidden(db_session, person, monkeypatch):
    vendor = Vendor(name="Active Vendor", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    db_session.refresh(vendor)

    link = VendorUser(vendor_id=vendor.id, person_id=person.id, is_active=True)
    db_session.add(link)

    person.is_active = False
    person.status = PersonStatus.inactive
    db_session.add(person)

    auth_session = AuthSession(
        person_id=person.id,
        status=SessionStatus.active,
        token_hash="test",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(auth_session)
    db_session.commit()
    db_session.refresh(auth_session)

    monkeypatch.setattr(
        vendor_portal.auth_flow_service,
        "decode_access_token",
        lambda _db, _token: {"sub": str(person.id), "session_id": str(auth_session.id)},
    )

    with pytest.raises(HTTPException) as excinfo:
        vendor_portal._session_from_access_token(db_session, "fake-access-token", username=person.email, remember=False)
    assert excinfo.value.status_code == 403
