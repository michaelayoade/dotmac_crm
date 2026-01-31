"""Tests for chat widget public API endpoints."""

import pytest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.models.crm.chat_widget import ChatWidgetConfig, WidgetVisitorSession
from app.schemas.crm.chat_widget import ChatWidgetConfigCreate
from app.services.crm.chat_widget import widget_configs, widget_visitors


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def db_session():
    """Create a test database session."""
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@pytest.fixture
def widget_config(db_session):
    """Create a test widget configuration."""
    return widget_configs.create(
        db_session,
        ChatWidgetConfigCreate(
            name="Test API Widget",
            allowed_domains=["localhost", "*.localhost"],
        ),
    )


@pytest.fixture
def visitor_session(db_session, widget_config):
    """Create a test visitor session."""
    from app.schemas.crm.chat_widget import WidgetSessionCreate

    session, token = widget_visitors.create_session(
        db_session,
        str(widget_config.id),
        WidgetSessionCreate(fingerprint="test-fp"),
    )
    return session, token


class TestGetWidgetConfig:
    """Tests for GET /widget/{config_id}/config endpoint."""

    def test_get_config_valid_origin(self, client, db_session, widget_config):
        """Test getting config with valid origin."""
        response = client.get(
            f"/widget/{widget_config.id}/config",
            headers={"Origin": "http://localhost"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["widget_id"] == str(widget_config.id)
        assert data["primary_color"] == widget_config.primary_color
        assert "widget_title" in data

    def test_get_config_invalid_origin_403(self, client, db_session):
        """Test that invalid origin returns 403."""
        config = widget_configs.create(
            db_session,
            ChatWidgetConfigCreate(
                name="Restricted Widget",
                allowed_domains=["example.com"],
            ),
        )

        response = client.get(
            f"/widget/{config.id}/config",
            headers={"Origin": "http://evil.com"},
        )

        assert response.status_code == 403

    def test_get_config_not_found(self, client):
        """Test getting non-existent config returns 404."""
        response = client.get(
            f"/widget/{uuid4()}/config",
            headers={"Origin": "http://localhost"},
        )

        assert response.status_code == 404

    def test_get_config_inactive_returns_404(self, client, db_session):
        """Test that inactive widget returns 404."""
        from app.schemas.crm.chat_widget import ChatWidgetConfigUpdate

        config = widget_configs.create(
            db_session,
            ChatWidgetConfigCreate(name="Inactive Widget"),
        )
        widget_configs.update(
            db_session,
            str(config.id),
            ChatWidgetConfigUpdate(is_active=False),
        )

        response = client.get(
            f"/widget/{config.id}/config",
            headers={"Origin": "http://localhost"},
        )

        assert response.status_code == 404


class TestCreateSession:
    """Tests for POST /widget/{config_id}/session endpoint."""

    def test_create_session_success(self, client, db_session, widget_config):
        """Test successful session creation."""
        response = client.post(
            f"/widget/{widget_config.id}/session",
            json={"fingerprint": "test-fp-123"},
            headers={"Origin": "http://localhost"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert "visitor_token" in data
        assert data["is_identified"] is False

    def test_create_session_with_page_url(self, client, db_session, widget_config):
        """Test session creation with page URL."""
        response = client.post(
            f"/widget/{widget_config.id}/session",
            json={
                "fingerprint": "test-fp-page",
                "page_url": "https://localhost/products",
            },
            headers={"Origin": "http://localhost"},
        )

        assert response.status_code == 200

    def test_create_session_resumes_existing(self, client, db_session, widget_config):
        """Test that same fingerprint returns same session."""
        # Create first session
        resp1 = client.post(
            f"/widget/{widget_config.id}/session",
            json={"fingerprint": "resume-fp"},
            headers={"Origin": "http://localhost"},
        )

        # Create second session with same fingerprint
        resp2 = client.post(
            f"/widget/{widget_config.id}/session",
            json={"fingerprint": "resume-fp"},
            headers={"Origin": "http://localhost"},
        )

        assert resp1.json()["session_id"] == resp2.json()["session_id"]
        assert resp1.json()["visitor_token"] == resp2.json()["visitor_token"]


class TestIdentifyVisitor:
    """Tests for POST /widget/session/{session_id}/identify endpoint."""

    def test_identify_visitor_success(
        self, client, db_session, widget_config, visitor_session
    ):
        """Test successful visitor identification."""
        session, token = visitor_session

        response = client.post(
            f"/widget/session/{session.id}/identify",
            json={
                "email": "user@example.com",
                "name": "Test User",
            },
            headers={
                "Origin": "http://localhost",
                "X-Visitor-Token": token,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "user@example.com"
        assert data["name"] == "Test User"
        assert "person_id" in data

    def test_identify_visitor_invalid_token_401(self, client, db_session, visitor_session):
        """Test that invalid token returns 401."""
        session, _ = visitor_session

        response = client.post(
            f"/widget/session/{session.id}/identify",
            json={"email": "user@example.com"},
            headers={
                "Origin": "http://localhost",
                "X-Visitor-Token": "invalid-token",
            },
        )

        assert response.status_code == 401

    def test_identify_visitor_session_mismatch_403(
        self, client, db_session, widget_config, visitor_session
    ):
        """Test that mismatched session ID returns 403."""
        _, token = visitor_session

        response = client.post(
            f"/widget/session/{uuid4()}/identify",
            json={"email": "user@example.com"},
            headers={
                "Origin": "http://localhost",
                "X-Visitor-Token": token,
            },
        )

        assert response.status_code == 403


class TestSendMessage:
    """Tests for POST /widget/session/{session_id}/message endpoint."""

    def test_send_message_success(self, client, db_session, widget_config, visitor_session):
        """Test successful message send."""
        session, token = visitor_session

        response = client.post(
            f"/widget/session/{session.id}/message",
            json={"body": "Hello, I need help!"},
            headers={
                "Origin": "http://localhost",
                "X-Visitor-Token": token,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "message_id" in data
        assert "conversation_id" in data
        assert data["status"] == "received"

    def test_send_message_invalid_token_401(self, client, db_session, visitor_session):
        """Test that invalid token returns 401."""
        session, _ = visitor_session

        response = client.post(
            f"/widget/session/{session.id}/message",
            json={"body": "Test"},
            headers={
                "Origin": "http://localhost",
                "X-Visitor-Token": "bad-token",
            },
        )

        assert response.status_code == 401

    def test_send_message_empty_body_422(self, client, db_session, visitor_session):
        """Test that empty body returns 422."""
        session, token = visitor_session

        response = client.post(
            f"/widget/session/{session.id}/message",
            json={"body": ""},
            headers={
                "Origin": "http://localhost",
                "X-Visitor-Token": token,
            },
        )

        assert response.status_code == 422


class TestGetMessages:
    """Tests for GET /widget/session/{session_id}/messages endpoint."""

    def test_get_messages_empty_conversation(
        self, client, db_session, widget_config, visitor_session
    ):
        """Test getting messages when no conversation exists."""
        session, token = visitor_session

        response = client.get(
            f"/widget/session/{session.id}/messages",
            headers={
                "Origin": "http://localhost",
                "X-Visitor-Token": token,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["messages"] == []
        assert data["has_more"] is False

    def test_get_messages_with_conversation(
        self, client, db_session, widget_config, visitor_session
    ):
        """Test getting messages with existing conversation."""
        session, token = visitor_session

        # Send some messages first
        for i in range(3):
            client.post(
                f"/widget/session/{session.id}/message",
                json={"body": f"Message {i}"},
                headers={
                    "Origin": "http://localhost",
                    "X-Visitor-Token": token,
                },
            )

        response = client.get(
            f"/widget/session/{session.id}/messages",
            headers={
                "Origin": "http://localhost",
                "X-Visitor-Token": token,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["messages"]) == 3

    def test_get_messages_pagination(self, client, db_session, widget_config, visitor_session):
        """Test message pagination."""
        session, token = visitor_session

        # Send messages
        for i in range(5):
            client.post(
                f"/widget/session/{session.id}/message",
                json={"body": f"Message {i}"},
                headers={
                    "Origin": "http://localhost",
                    "X-Visitor-Token": token,
                },
            )

        # Get first page
        response = client.get(
            f"/widget/session/{session.id}/messages?limit=2",
            headers={
                "Origin": "http://localhost",
                "X-Visitor-Token": token,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["messages"]) == 2
        assert data["has_more"] is True


class TestSessionStatus:
    """Tests for GET /widget/session/{session_id}/status endpoint."""

    def test_get_session_status(self, client, db_session, widget_config, visitor_session):
        """Test getting session status."""
        session, token = visitor_session

        response = client.get(
            f"/widget/session/{session.id}/status",
            headers={
                "Origin": "http://localhost",
                "X-Visitor-Token": token,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == str(session.id)
        assert data["is_identified"] is False
        assert data["unread_count"] == 0
