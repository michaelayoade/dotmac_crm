"""Tests for chat widget service layer."""

import pytest
from datetime import datetime, timezone
from uuid import uuid4

from app.models.crm.chat_widget import ChatWidgetConfig, WidgetVisitorSession
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, MessageDirection, MessageStatus
from app.models.person import Person
from app.schemas.crm.chat_widget import (
    ChatWidgetConfigCreate,
    ChatWidgetConfigUpdate,
    WidgetSessionCreate,
)
from app.services.crm.chat_widget import (
    ChatWidgetConfigManager,
    WidgetVisitorManager,
    receive_widget_message,
    widget_configs,
    widget_visitors,
)


class TestChatWidgetConfigManager:
    """Tests for ChatWidgetConfigManager."""

    def test_create_widget_config(self, db_session):
        """Test creating a widget configuration."""
        payload = ChatWidgetConfigCreate(
            name="Test Widget",
            allowed_domains=["example.com", "*.example.com"],
            primary_color="#FF5733",
            welcome_message="Hello!",
        )

        config = widget_configs.create(db_session, payload)

        assert config.id is not None
        assert config.name == "Test Widget"
        assert config.allowed_domains == ["example.com", "*.example.com"]
        assert config.primary_color == "#FF5733"
        assert config.welcome_message == "Hello!"
        assert config.is_active is True

    def test_get_widget_config(self, db_session):
        """Test retrieving a widget configuration."""
        payload = ChatWidgetConfigCreate(name="Get Test Widget")
        created = widget_configs.create(db_session, payload)

        fetched = widget_configs.get(db_session, str(created.id))

        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.name == "Get Test Widget"

    def test_update_widget_config(self, db_session):
        """Test updating a widget configuration."""
        payload = ChatWidgetConfigCreate(name="Original Name")
        config = widget_configs.create(db_session, payload)

        update_payload = ChatWidgetConfigUpdate(
            name="Updated Name",
            primary_color="#00FF00",
            is_active=False,
        )
        updated = widget_configs.update(db_session, str(config.id), update_payload)

        assert updated is not None
        assert updated.name == "Updated Name"
        assert updated.primary_color == "#00FF00"
        assert updated.is_active is False

    def test_list_widget_configs(self, db_session):
        """Test listing widget configurations."""
        # Create a few widgets
        for i in range(3):
            widget_configs.create(
                db_session,
                ChatWidgetConfigCreate(name=f"Widget {i}"),
            )

        configs = widget_configs.list(db_session)
        assert len(configs) >= 3

    def test_list_active_only(self, db_session):
        """Test listing only active widgets."""
        active = widget_configs.create(
            db_session,
            ChatWidgetConfigCreate(name="Active Widget"),
        )
        inactive = widget_configs.create(
            db_session,
            ChatWidgetConfigCreate(name="Inactive Widget"),
        )
        widget_configs.update(
            db_session,
            str(inactive.id),
            ChatWidgetConfigUpdate(is_active=False),
        )

        active_configs = widget_configs.list(db_session, is_active=True)
        names = [c.name for c in active_configs]

        assert "Active Widget" in names
        assert "Inactive Widget" not in names

    def test_delete_widget_config(self, db_session):
        """Test deleting a widget configuration."""
        config = widget_configs.create(
            db_session,
            ChatWidgetConfigCreate(name="To Delete"),
        )
        config_id = str(config.id)

        result = widget_configs.delete(db_session, config_id)
        assert result is True

        fetched = widget_configs.get(db_session, config_id)
        assert fetched is None

    def test_validate_origin_exact_match(self, db_session):
        """Test origin validation with exact domain match."""
        config = widget_configs.create(
            db_session,
            ChatWidgetConfigCreate(
                name="Origin Test",
                allowed_domains=["example.com", "test.com"],
            ),
        )

        assert widget_configs.validate_origin(config, "https://example.com") is True
        assert widget_configs.validate_origin(config, "https://test.com") is True
        assert widget_configs.validate_origin(config, "https://other.com") is False

    def test_validate_origin_wildcard(self, db_session):
        """Test origin validation with wildcard subdomain."""
        config = widget_configs.create(
            db_session,
            ChatWidgetConfigCreate(
                name="Wildcard Test",
                allowed_domains=["*.example.com"],
            ),
        )

        assert widget_configs.validate_origin(config, "https://sub.example.com") is True
        assert widget_configs.validate_origin(config, "https://deep.sub.example.com") is True
        assert widget_configs.validate_origin(config, "https://example.com") is True
        assert widget_configs.validate_origin(config, "https://other.com") is False

    def test_validate_origin_no_restrictions(self, db_session):
        """Test origin validation with no domain restrictions."""
        config = widget_configs.create(
            db_session,
            ChatWidgetConfigCreate(
                name="No Restrictions",
                allowed_domains=[],
            ),
        )

        assert widget_configs.validate_origin(config, "https://any.com") is True
        assert widget_configs.validate_origin(config, None) is True

    def test_validate_origin_rejects_missing(self, db_session):
        """Test that missing origin is rejected when domains are configured."""
        config = widget_configs.create(
            db_session,
            ChatWidgetConfigCreate(
                name="Requires Origin",
                allowed_domains=["example.com"],
            ),
        )

        assert widget_configs.validate_origin(config, None) is False
        assert widget_configs.validate_origin(config, "") is False

    def test_generate_embed_code(self, db_session):
        """Test embed code generation."""
        config = widget_configs.create(
            db_session,
            ChatWidgetConfigCreate(name="Embed Test"),
        )

        embed_code = widget_configs.generate_embed_code(config, "https://api.example.com")

        assert f"configId: '{config.id}'" in embed_code
        assert "apiUrl: 'https://api.example.com'" in embed_code
        assert "chat-widget.js" in embed_code


class TestWidgetVisitorManager:
    """Tests for WidgetVisitorManager."""

    def test_create_visitor_session(self, db_session):
        """Test creating a visitor session."""
        config = widget_configs.create(
            db_session,
            ChatWidgetConfigCreate(name="Session Test"),
        )

        payload = WidgetSessionCreate(
            fingerprint="test-fingerprint-123",
            page_url="https://example.com/page",
        )

        session, token = widget_visitors.create_session(
            db_session,
            str(config.id),
            payload,
            ip_address="192.168.1.1",
            user_agent="Test Agent",
        )

        assert session.id is not None
        assert session.visitor_token == token
        assert session.widget_config_id == config.id
        assert session.fingerprint_hash is not None
        assert session.ip_address == "192.168.1.1"
        assert session.page_url == "https://example.com/page"

    def test_session_resume_by_fingerprint(self, db_session):
        """Test that same fingerprint returns existing session."""
        config = widget_configs.create(
            db_session,
            ChatWidgetConfigCreate(name="Resume Test"),
        )

        payload = WidgetSessionCreate(fingerprint="same-fingerprint")

        session1, token1 = widget_visitors.create_session(
            db_session,
            str(config.id),
            payload,
        )
        session2, token2 = widget_visitors.create_session(
            db_session,
            str(config.id),
            payload,
        )

        assert session1.id == session2.id
        assert token1 == token2

    def test_get_session_by_token(self, db_session):
        """Test retrieving session by visitor token."""
        config = widget_configs.create(
            db_session,
            ChatWidgetConfigCreate(name="Token Test"),
        )

        session, token = widget_visitors.create_session(
            db_session,
            str(config.id),
            WidgetSessionCreate(),
        )

        fetched = widget_visitors.get_session_by_token(db_session, token)
        assert fetched is not None
        assert fetched.id == session.id

    def test_identify_visitor_new_person(self, db_session):
        """Test identifying visitor creates new person."""
        config = widget_configs.create(
            db_session,
            ChatWidgetConfigCreate(name="Identify Test"),
        )

        session, _ = widget_visitors.create_session(
            db_session,
            str(config.id),
            WidgetSessionCreate(),
        )

        identified = widget_visitors.identify_visitor(
            db_session,
            session,
            email="newuser@example.com",
            name="New User",
            custom_fields={"company": "Test Inc"},
        )

        assert identified.is_identified is True
        assert identified.identified_email == "newuser@example.com"
        assert identified.identified_name == "New User"
        assert identified.person_id is not None
        assert identified.metadata_.get("company") == "Test Inc"

    def test_identify_visitor_existing_person(self, db_session):
        """Test identifying visitor links to existing person."""
        # Create existing person
        person = Person(
            email="existing@example.com",
            first_name="Existing",
            last_name="User",
        )
        db_session.add(person)
        db_session.commit()

        config = widget_configs.create(
            db_session,
            ChatWidgetConfigCreate(name="Existing Test"),
        )

        session, _ = widget_visitors.create_session(
            db_session,
            str(config.id),
            WidgetSessionCreate(),
        )

        identified = widget_visitors.identify_visitor(
            db_session,
            session,
            email="existing@example.com",
            name="Different Name",
        )

        assert identified.person_id == person.id

    def test_check_rate_limit(self, db_session):
        """Test rate limiting for message sends."""
        config = widget_configs.create(
            db_session,
            ChatWidgetConfigCreate(
                name="Rate Limit Test",
                rate_limit_messages_per_minute=3,
            ),
        )

        session, _ = widget_visitors.create_session(
            db_session,
            str(config.id),
            WidgetSessionCreate(),
        )

        # Without a conversation, should always be True
        assert widget_visitors.check_rate_limit(db_session, session, config) is True


class TestReceiveWidgetMessage:
    """Tests for receive_widget_message function."""

    def test_receive_message_creates_conversation(self, db_session):
        """Test that receiving a message creates a conversation."""
        config = widget_configs.create(
            db_session,
            ChatWidgetConfigCreate(name="Message Test"),
        )

        session, _ = widget_visitors.create_session(
            db_session,
            str(config.id),
            WidgetSessionCreate(),
        )

        message = receive_widget_message(
            db_session,
            session,
            body="Hello, I need help!",
        )

        assert message.id is not None
        assert message.body == "Hello, I need help!"
        assert message.channel_type == ChannelType.chat_widget
        assert message.direction == MessageDirection.inbound
        assert message.status == MessageStatus.received
        assert message.conversation_id is not None

        # Session should be updated with conversation
        db_session.refresh(session)
        assert session.conversation_id == message.conversation_id

    def test_receive_message_uses_existing_conversation(self, db_session):
        """Test that subsequent messages use existing conversation."""
        config = widget_configs.create(
            db_session,
            ChatWidgetConfigCreate(name="Existing Conv Test"),
        )

        session, _ = widget_visitors.create_session(
            db_session,
            str(config.id),
            WidgetSessionCreate(),
        )

        msg1 = receive_widget_message(db_session, session, body="First message")
        msg2 = receive_widget_message(db_session, session, body="Second message")

        assert msg1.conversation_id == msg2.conversation_id

    def test_message_sanitization_strips_html(self, db_session):
        """Test that HTML is stripped from message body."""
        config = widget_configs.create(
            db_session,
            ChatWidgetConfigCreate(name="Sanitize Test"),
        )

        session, _ = widget_visitors.create_session(
            db_session,
            str(config.id),
            WidgetSessionCreate(),
        )

        message = receive_widget_message(
            db_session,
            session,
            body="<script>alert('xss')</script>Hello!",
        )

        assert "<script>" not in message.body
        assert "Hello!" in message.body


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
