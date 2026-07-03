"""Tests for vendor bearer-token auth (mobile field app)."""

import pyotp
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from starlette.requests import Request

from app.api.vendor_auth import vendor_me
from app.models.auth import AuthProvider, SessionStatus, UserCredential
from app.models.auth import Session as AuthSession
from app.models.vendor import Vendor, VendorUser
from app.services.auth_dependencies import require_permission
from app.services.auth_flow import AuthFlow, hash_password
from app.services.vendor_auth_tokens import require_vendor_token, vendor_auth_tokens


def _make_request():
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/vendor/auth",
        "headers": [(b"user-agent", b"pytest")],
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope)


@pytest.fixture()
def vendor(db_session):
    vendor = Vendor(name="FiberWorks Ltd", is_active=True)
    db_session.add(vendor)
    db_session.commit()
    db_session.refresh(vendor)
    return vendor


@pytest.fixture()
def vendor_user(db_session, vendor, person):
    link = VendorUser(vendor_id=vendor.id, person_id=person.id, role="crew_lead", is_active=True)
    db_session.add(link)
    db_session.commit()
    db_session.refresh(link)
    return link


@pytest.fixture()
def credential(db_session, person):
    credential = UserCredential(
        person_id=person.id,
        provider=AuthProvider.local,
        username=person.email,
        password_hash=hash_password("secret"),
        is_active=True,
    )
    db_session.add(credential)
    db_session.commit()
    return credential


def test_vendor_login_returns_tokens_and_context(db_session, vendor, vendor_user, credential, person):
    result = vendor_auth_tokens.login(db_session, person.email, "secret", _make_request())
    assert result["access_token"]
    assert result["refresh_token"]
    assert result["vendor_id"] == str(vendor.id)
    assert result["vendor_user_id"] == str(vendor_user.id)
    assert result["vendor_role"] == "crew_lead"


def test_non_vendor_login_rejected_and_session_revoked(db_session, credential, person):
    with pytest.raises(HTTPException) as exc:
        vendor_auth_tokens.login(db_session, person.email, "secret", _make_request())
    assert exc.value.status_code == 403
    # The session issued during login must not survive the rejection.
    active = (
        db_session.query(AuthSession)
        .filter(AuthSession.person_id == person.id)
        .filter(AuthSession.status == SessionStatus.active)
        .count()
    )
    assert active == 0


def test_vendor_mfa_flow(db_session, vendor, vendor_user, credential, person, monkeypatch):
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", Fernet.generate_key().decode("utf-8"))
    setup = AuthFlow.mfa_setup(db_session, str(person.id), label="phone")
    AuthFlow.mfa_confirm(db_session, str(setup["method_id"]), pyotp.TOTP(setup["secret"]).now(), str(person.id))

    challenge = vendor_auth_tokens.login(db_session, person.email, "secret", _make_request())
    assert challenge["mfa_required"] is True
    assert challenge["mfa_token"]

    result = vendor_auth_tokens.mfa_verify(
        db_session, challenge["mfa_token"], pyotp.TOTP(setup["secret"]).now(), _make_request()
    )
    assert result["access_token"]
    assert result["vendor_id"] == str(vendor.id)


def test_vendor_refresh_rotates_and_keeps_context(db_session, vendor, vendor_user, credential, person):
    tokens = vendor_auth_tokens.login(db_session, person.email, "secret", _make_request())
    refreshed = vendor_auth_tokens.refresh(db_session, tokens["refresh_token"], _make_request())
    assert refreshed["access_token"]
    assert refreshed["refresh_token"] != tokens["refresh_token"]
    assert refreshed["vendor_id"] == str(vendor.id)


def test_refresh_rejected_after_vendor_user_deactivated(db_session, vendor, vendor_user, credential, person):
    tokens = vendor_auth_tokens.login(db_session, person.email, "secret", _make_request())
    vendor_user.is_active = False
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        vendor_auth_tokens.refresh(db_session, tokens["refresh_token"], _make_request())
    assert exc.value.status_code == 401


def test_refresh_rejected_after_vendor_company_deactivated(db_session, vendor, vendor_user, credential, person):
    tokens = vendor_auth_tokens.login(db_session, person.email, "secret", _make_request())
    vendor.is_active = False
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        vendor_auth_tokens.refresh(db_session, tokens["refresh_token"], _make_request())
    assert exc.value.status_code == 401


def test_require_vendor_token_resolves_context(db_session, vendor, vendor_user, person):
    auth = {"person_id": str(person.id), "session_id": "s", "roles": [], "scopes": []}
    context = require_vendor_token(auth=auth, db=db_session)
    assert context["vendor_id"] == str(vendor.id)
    assert context["vendor_user"].id == vendor_user.id


def test_vendor_me_returns_vendor_profile_context(db_session, vendor, vendor_user, person):
    result = vendor_me(
        context={
            "person_id": str(person.id),
            "vendor_id": str(vendor.id),
            "vendor_user_id": str(vendor_user.id),
            "vendor_role": vendor_user.role,
            "vendor_user": vendor_user,
        }
    )
    assert result.name == f"{person.first_name} {person.last_name}"
    assert result.email == person.email
    assert result.vendor_name == vendor.name
    assert result.vendor_role == vendor_user.role


def test_require_vendor_token_rejects_non_vendor(db_session, person):
    auth = {"person_id": str(person.id), "session_id": "s", "roles": [], "scopes": []}
    with pytest.raises(HTTPException) as exc:
        require_vendor_token(auth=auth, db=db_session)
    assert exc.value.status_code == 403


def test_vendor_token_rejected_on_staff_endpoints(db_session, vendor, vendor_user, person):
    """A vendor person holds no staff roles/permissions, so RBAC rejects them."""
    guard = require_permission("operations:work_order:read")
    auth = {"person_id": str(person.id), "session_id": "s", "roles": [], "scopes": []}
    with pytest.raises(HTTPException) as exc:
        guard(auth=auth, db=db_session)
    assert exc.value.status_code == 403
