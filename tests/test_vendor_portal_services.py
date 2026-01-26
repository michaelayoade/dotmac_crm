"""Tests for vendor portal services."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException, Request

from app.models.auth import Session as AuthSession, SessionStatus
from app.models.vendor import Vendor, VendorUser
from app.services import vendor_portal


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture()
def vendor(db_session):
    """Create a test vendor."""
    vendor = Vendor(
        name="Test Portal Vendor",
        code="TPV001",
    )
    db_session.add(vendor)
    db_session.commit()
    db_session.refresh(vendor)
    return vendor


@pytest.fixture()
def vendor_user(db_session, person, vendor):
    """Create a vendor user linked to person."""
    vu = VendorUser(
        vendor_id=vendor.id,
        person_id=person.id,
        role="admin",
    )
    db_session.add(vu)
    db_session.commit()
    db_session.refresh(vu)
    return vu


@pytest.fixture()
def mock_request():
    """Create a mock request object."""
    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = "127.0.0.1"
    request.headers = {}
    return request


@pytest.fixture(autouse=True)
def clear_sessions():
    """Clear sessions before and after each test."""
    vendor_portal._VENDOR_SESSIONS.clear()
    yield
    vendor_portal._VENDOR_SESSIONS.clear()


# =============================================================================
# Session Management Tests
# =============================================================================


def test_create_session():
    """Test creating a vendor session."""
    token = vendor_portal._create_session(
        username="test@example.com",
        person_id="person-123",
        vendor_id="vendor-456",
        role="admin",
        remember=False,
    )
    assert token is not None
    assert len(token) > 20  # URL-safe token is reasonably long
    assert token in vendor_portal._VENDOR_SESSIONS


def test_get_session_valid():
    """Test getting a valid session."""
    token = vendor_portal._create_session(
        username="test@example.com",
        person_id="person-123",
        vendor_id="vendor-456",
        role="admin",
        remember=False,
    )

    session = vendor_portal._get_session(token)
    assert session is not None
    assert session["username"] == "test@example.com"
    assert session["person_id"] == "person-123"
    assert session["vendor_id"] == "vendor-456"
    assert session["role"] == "admin"


def test_get_session_not_found():
    """Test getting a non-existent session."""
    session = vendor_portal._get_session("invalid-token")
    assert session is None


def test_get_session_expired():
    """Test getting an expired session returns None."""
    token = vendor_portal._create_session(
        username="test@example.com",
        person_id="person-123",
        vendor_id="vendor-456",
        role=None,
        remember=False,
    )

    # Manually expire the session
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    vendor_portal._VENDOR_SESSIONS[token]["expires_at"] = past

    session = vendor_portal._get_session(token)
    assert session is None
    # Session should be deleted
    assert token not in vendor_portal._VENDOR_SESSIONS


def test_invalidate_session():
    """Test invalidating a session."""
    token = vendor_portal._create_session(
        username="test@example.com",
        person_id="person-123",
        vendor_id="vendor-456",
        role=None,
        remember=False,
    )
    assert token in vendor_portal._VENDOR_SESSIONS

    vendor_portal.invalidate_session(token)
    assert token not in vendor_portal._VENDOR_SESSIONS


def test_invalidate_session_not_found():
    """Test invalidating a non-existent session does nothing."""
    # Should not raise
    vendor_portal.invalidate_session("non-existent-token")


# =============================================================================
# Initials Helper Tests
# =============================================================================


def test_initials_full_name(db_session, person):
    """Test initials with full name."""
    # Person fixture already has first_name and last_name set
    initials = vendor_portal._initials(person)
    # Person fixture sets first_name="Test", last_name="User"
    assert initials == "TU"


def test_initials_first_name_only():
    """Test initials with first name only."""
    # Use mock object to test edge case
    mock_person = MagicMock()
    mock_person.first_name = "Jane"
    mock_person.last_name = None

    initials = vendor_portal._initials(mock_person)
    assert initials == "J"


def test_initials_last_name_only():
    """Test initials with last name only."""
    mock_person = MagicMock()
    mock_person.first_name = None
    mock_person.last_name = "Smith"

    initials = vendor_portal._initials(mock_person)
    assert initials == "S"


def test_initials_empty_names():
    """Test initials with empty names returns default."""
    mock_person = MagicMock()
    mock_person.first_name = ""
    mock_person.last_name = ""

    initials = vendor_portal._initials(mock_person)
    assert initials == "VD"


def test_initials_none_names():
    """Test initials with None names returns default."""
    mock_person = MagicMock()
    mock_person.first_name = None
    mock_person.last_name = None

    initials = vendor_portal._initials(mock_person)
    assert initials == "VD"


# =============================================================================
# Get Vendor User Tests
# =============================================================================


def test_get_vendor_user_found(db_session, vendor_user, person):
    """Test finding a vendor user."""
    result = vendor_portal._get_vendor_user(db_session, str(person.id))
    assert result is not None
    assert result.id == vendor_user.id


def test_get_vendor_user_not_found(db_session):
    """Test vendor user not found returns None."""
    result = vendor_portal._get_vendor_user(db_session, str(uuid.uuid4()))
    assert result is None


def test_get_vendor_user_inactive(db_session, vendor_user, person):
    """Test inactive vendor user returns None."""
    vendor_user.is_active = False
    db_session.commit()

    result = vendor_portal._get_vendor_user(db_session, str(person.id))
    assert result is None


# =============================================================================
# Login Tests
# =============================================================================


def test_login_success(db_session, vendor_user, person, mock_request):
    """Test successful vendor login."""
    import hashlib
    token_hash = hashlib.sha256(b"test-session-token").hexdigest()

    # Use naive datetime to match SQLite
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    expires_naive = now_naive + timedelta(hours=24)

    # Create auth session
    auth_session = AuthSession(
        person_id=person.id,
        status=SessionStatus.active,
        token_hash=token_hash,
        expires_at=expires_naive,
    )
    db_session.add(auth_session)
    db_session.commit()

    mock_login_result = {
        "access_token": "test-token",
    }
    mock_payload = {
        "sub": str(person.id),
        "session_id": str(auth_session.id),
    }

    # Patch auth_flow_service to add login method and decode_access_token
    with patch.object(vendor_portal, "auth_flow_service") as mock_auth:
        mock_auth.auth_flow.login.return_value = mock_login_result
        mock_auth.decode_access_token.return_value = mock_payload
        # Mock _now to return naive datetime for comparison
        with patch.object(vendor_portal, "_now", return_value=now_naive):
            result = vendor_portal.login(
                db_session, "test@example.com", "password123", mock_request, False
            )

    assert "session_token" in result
    assert "vendor_id" in result


def test_login_mfa_required(db_session, mock_request):
    """Test login with MFA required."""
    mock_login_result = {
        "mfa_required": True,
        "mfa_token": "mfa-test-token",
    }

    with patch.object(vendor_portal, "auth_flow_service") as mock_auth:
        mock_auth.auth_flow.login.return_value = mock_login_result
        result = vendor_portal.login(
            db_session, "test@example.com", "password123", mock_request, False
        )

    assert result.get("mfa_required") is True
    assert result.get("mfa_token") == "mfa-test-token"


def test_login_invalid_credentials(db_session, mock_request):
    """Test login with invalid credentials."""
    mock_login_result = {}  # No access token

    with patch.object(vendor_portal, "auth_flow_service") as mock_auth:
        mock_auth.auth_flow.login.return_value = mock_login_result
        with pytest.raises(HTTPException) as exc_info:
            vendor_portal.login(
                db_session, "test@example.com", "wrong-password", mock_request, False
            )
    assert exc_info.value.status_code == 401
    assert "Invalid credentials" in exc_info.value.detail


def test_login_no_vendor_access(db_session, person, mock_request):
    """Test login for user without vendor access."""
    import hashlib
    token_hash = hashlib.sha256(b"test-no-vendor-token").hexdigest()

    # Use naive datetime to match SQLite
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    expires_naive = now_naive + timedelta(hours=24)

    # Create auth session but no VendorUser
    auth_session = AuthSession(
        person_id=person.id,
        status=SessionStatus.active,
        token_hash=token_hash,
        expires_at=expires_naive,
    )
    db_session.add(auth_session)
    db_session.commit()

    mock_login_result = {"access_token": "test-token"}
    mock_payload = {
        "sub": str(person.id),
        "session_id": str(auth_session.id),
    }

    with patch.object(vendor_portal, "auth_flow_service") as mock_auth:
        mock_auth.auth_flow.login.return_value = mock_login_result
        mock_auth.decode_access_token.return_value = mock_payload
        with patch.object(vendor_portal, "_now", return_value=now_naive):
            with pytest.raises(HTTPException) as exc_info:
                vendor_portal.login(
                    db_session, "test@example.com", "password123", mock_request, False
                )
    assert exc_info.value.status_code == 403
    assert "Vendor access required" in exc_info.value.detail


# =============================================================================
# MFA Verification Tests
# =============================================================================


def test_verify_mfa_success(db_session, vendor_user, person, mock_request):
    """Test successful MFA verification."""
    import hashlib
    token_hash = hashlib.sha256(b"mfa-session-token").hexdigest()

    # Use naive datetime to match SQLite
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    expires_naive = now_naive + timedelta(hours=24)

    auth_session = AuthSession(
        person_id=person.id,
        status=SessionStatus.active,
        token_hash=token_hash,
        expires_at=expires_naive,
    )
    db_session.add(auth_session)
    db_session.commit()

    mock_verify_result = {"access_token": "verified-token"}
    mock_payload = {
        "sub": str(person.id),
        "session_id": str(auth_session.id),
    }

    with patch.object(vendor_portal, "auth_flow_service") as mock_auth:
        mock_auth.auth_flow.mfa_verify.return_value = mock_verify_result
        mock_auth.decode_access_token.return_value = mock_payload
        with patch.object(vendor_portal, "_now", return_value=now_naive):
            result = vendor_portal.verify_mfa(
                db_session, "mfa-token", "123456", mock_request, False
            )

    assert "session_token" in result
    assert "vendor_id" in result


def test_verify_mfa_invalid_code(db_session, mock_request):
    """Test MFA verification with invalid code."""
    mock_verify_result = {}  # No access token

    with patch.object(vendor_portal, "auth_flow_service") as mock_auth:
        mock_auth.auth_flow.mfa_verify.return_value = mock_verify_result
        with pytest.raises(HTTPException) as exc_info:
            vendor_portal.verify_mfa(
                db_session, "mfa-token", "wrong-code", mock_request, False
            )
    assert exc_info.value.status_code == 401
    assert "Invalid verification code" in exc_info.value.detail


# =============================================================================
# Session from Access Token Tests
# =============================================================================


def test_session_from_access_token_invalid_payload(db_session):
    """Test session creation with invalid token payload."""
    mock_payload = {}  # Missing sub and session_id

    with patch.object(vendor_portal, "auth_flow_service") as mock_auth:
        mock_auth.decode_access_token.return_value = mock_payload
        with pytest.raises(HTTPException) as exc_info:
            vendor_portal._session_from_access_token(
                db_session, "token", "username", False
            )
    assert exc_info.value.status_code == 401
    assert "Invalid session" in exc_info.value.detail


def test_session_from_access_token_inactive_session(db_session, person):
    """Test session creation with inactive auth session."""
    import hashlib
    token_hash = hashlib.sha256(b"inactive-session-token").hexdigest()

    auth_session = AuthSession(
        person_id=person.id,
        status=SessionStatus.revoked,
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db_session.add(auth_session)
    db_session.commit()

    mock_payload = {
        "sub": str(person.id),
        "session_id": str(auth_session.id),
    }

    with patch.object(vendor_portal, "auth_flow_service") as mock_auth:
        mock_auth.decode_access_token.return_value = mock_payload
        with pytest.raises(HTTPException) as exc_info:
            vendor_portal._session_from_access_token(
                db_session, "token", "username", False
            )
    assert exc_info.value.status_code == 401
    assert "Invalid session" in exc_info.value.detail


def test_session_from_access_token_session_not_found(db_session, person):
    """Test session creation when auth session not found."""
    mock_payload = {
        "sub": str(person.id),
        "session_id": str(uuid.uuid4()),  # Non-existent session
    }

    with patch.object(vendor_portal, "auth_flow_service") as mock_auth:
        mock_auth.decode_access_token.return_value = mock_payload
        with pytest.raises(HTTPException) as exc_info:
            vendor_portal._session_from_access_token(
                db_session, "token", "username", False
            )
    assert exc_info.value.status_code == 401
    assert "Invalid session" in exc_info.value.detail


def test_session_from_access_token_expired_session(db_session, person):
    """Test session creation with expired auth session."""
    import hashlib
    token_hash = hashlib.sha256(b"expired-session-token").hexdigest()

    auth_session = AuthSession(
        person_id=person.id,
        status=SessionStatus.active,
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),  # Expired
    )
    db_session.add(auth_session)
    db_session.commit()

    mock_payload = {
        "sub": str(person.id),
        "session_id": str(auth_session.id),
    }

    # Use naive datetime for SQLite
    now_naive = datetime.now(timezone.utc).replace(tzinfo=None)

    with patch.object(vendor_portal, "auth_flow_service") as mock_auth:
        mock_auth.decode_access_token.return_value = mock_payload
        with patch.object(vendor_portal, "_now", return_value=now_naive):
            with pytest.raises(HTTPException) as exc_info:
                vendor_portal._session_from_access_token(
                    db_session, "token", "username", False
                )
    assert exc_info.value.status_code == 401
    assert "Session expired" in exc_info.value.detail


# =============================================================================
# Get Context Tests
# =============================================================================


def test_get_context_valid_session(db_session, vendor_user, person, vendor):
    """Test getting context with valid session."""
    person.first_name = "Test"
    person.last_name = "User"
    db_session.commit()

    token = vendor_portal._create_session(
        username="test@example.com",
        person_id=str(person.id),
        vendor_id=str(vendor.id),
        role="admin",
        remember=False,
    )

    context = vendor_portal.get_context(db_session, token)
    assert context is not None
    assert context["session"]["username"] == "test@example.com"
    assert context["current_user"]["initials"] == "TU"
    assert context["person"].id == person.id
    assert context["vendor"].id == vendor.id
    assert context["vendor_user"].id == vendor_user.id


def test_get_context_no_session_token(db_session):
    """Test getting context with no session token."""
    context = vendor_portal.get_context(db_session, None)
    assert context is None


def test_get_context_invalid_token(db_session):
    """Test getting context with invalid session token."""
    context = vendor_portal.get_context(db_session, "invalid-token")
    assert context is None


def test_get_context_person_not_found(db_session, vendor):
    """Test getting context when person not found."""
    token = vendor_portal._create_session(
        username="test@example.com",
        person_id=str(uuid.uuid4()),  # Non-existent person
        vendor_id=str(vendor.id),
        role=None,
        remember=False,
    )

    context = vendor_portal.get_context(db_session, token)
    assert context is None


def test_get_context_vendor_not_found(db_session, person):
    """Test getting context when vendor not found."""
    token = vendor_portal._create_session(
        username="test@example.com",
        person_id=str(person.id),
        vendor_id=str(uuid.uuid4()),  # Non-existent vendor
        role=None,
        remember=False,
    )

    context = vendor_portal.get_context(db_session, token)
    assert context is None


def test_get_context_vendor_user_not_found(db_session, person, vendor):
    """Test getting context when vendor user not found."""
    # Person and vendor exist, but no VendorUser link
    token = vendor_portal._create_session(
        username="test@example.com",
        person_id=str(person.id),
        vendor_id=str(vendor.id),
        role=None,
        remember=False,
    )

    context = vendor_portal.get_context(db_session, token)
    assert context is None
