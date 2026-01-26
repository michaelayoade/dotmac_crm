"""Tests for CRM email polling service."""

import email
from email.message import Message
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.connector import ConnectorConfig, ConnectorType
from app.services.crm import email_polling


# =============================================================================
# Helper Function Tests
# =============================================================================


def test_decode_header_none():
    """Test decoding None header returns None."""
    result = email_polling._decode_header(None)
    assert result is None


def test_decode_header_empty():
    """Test decoding empty header returns None."""
    result = email_polling._decode_header("")
    assert result is None


def test_decode_header_plain():
    """Test decoding plain ASCII header."""
    result = email_polling._decode_header("Hello World")
    assert result == "Hello World"


def test_decode_header_encoded():
    """Test decoding encoded header."""
    # Encoded subject
    result = email_polling._decode_header("=?utf-8?b?SGVsbG8gV29ybGQ=?=")
    assert result == "Hello World"


def test_extract_body_simple():
    """Test extracting body from simple message."""
    msg = Message()
    msg.set_payload(b"Test body content")
    msg.set_type("text/plain")

    result = email_polling._extract_body(msg)
    assert result == "Test body content"


def test_extract_body_multipart_text():
    """Test extracting text body from multipart message."""
    msg = MagicMock()
    msg.is_multipart.return_value = True

    text_part = MagicMock()
    text_part.get_content_type.return_value = "text/plain"
    text_part.get.return_value = ""
    text_part.get_payload.return_value = b"Plain text content"
    text_part.get_content_charset.return_value = "utf-8"

    msg.walk.return_value = [text_part]

    result = email_polling._extract_body(msg)
    assert result == "Plain text content"


def test_extract_body_multipart_html_fallback():
    """Test extracting HTML body when no plain text available."""
    msg = MagicMock()
    msg.is_multipart.return_value = True

    html_part = MagicMock()
    html_part.get_content_type.return_value = "text/html"
    html_part.get.return_value = ""
    html_part.get_payload.return_value = b"<html>HTML content</html>"
    html_part.get_content_charset.return_value = "utf-8"

    # First walk returns no plain text, second walk returns HTML
    def walk_generator():
        yield html_part

    msg.walk.return_value = list(walk_generator())

    # Need to mock walk to be called twice
    msg.walk.side_effect = [
        [html_part],  # First call - looking for text/plain
        [html_part],  # Second call - looking for text/html
    ]

    result = email_polling._extract_body(msg)
    assert "HTML content" in result


def test_extract_body_multipart_empty():
    """Test extracting body from multipart with no text parts."""
    msg = MagicMock()
    msg.is_multipart.return_value = True

    attachment_part = MagicMock()
    attachment_part.get_content_type.return_value = "application/pdf"
    attachment_part.get.return_value = "attachment"

    msg.walk.return_value = [attachment_part]

    result = email_polling._extract_body(msg)
    assert result == ""


# =============================================================================
# IMAP Polling Tests
# =============================================================================


def test_imap_poll_incomplete_config(db_session):
    """Test IMAP poll with incomplete config raises 400."""
    config = ConnectorConfig(
        name="Incomplete IMAP",
        connector_type=ConnectorType.email,
        metadata_={"imap": {"host": "imap.example.com"}},
        auth_config={},  # Missing username/password
    )
    db_session.add(config)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        email_polling._imap_poll(
            db_session,
            config,
            config.metadata_["imap"],
            config.auth_config,
        )
    assert exc_info.value.status_code == 400
    assert "IMAP config incomplete" in exc_info.value.detail


def test_imap_poll_success(db_session):
    """Test successful IMAP poll."""
    config = ConnectorConfig(
        name="IMAP Success",
        connector_type=ConnectorType.email,
        metadata_={"imap": {"host": "imap.example.com", "port": 993}},
        auth_config={"username": "user@example.com", "password": "secret"},
    )
    db_session.add(config)
    db_session.commit()

    # Create mock IMAP client
    mock_client = MagicMock()
    mock_client.uid.return_value = ("OK", [b""])  # No messages

    with patch("imaplib.IMAP4_SSL", return_value=mock_client):
        result = email_polling._imap_poll(
            db_session,
            config,
            config.metadata_["imap"],
            config.auth_config,
        )

    assert result == 0
    mock_client.login.assert_called_once()
    mock_client.select.assert_called_once()
    mock_client.logout.assert_called_once()


def test_imap_poll_with_messages(db_session):
    """Test IMAP poll processing messages."""
    config = ConnectorConfig(
        name="IMAP With Messages",
        connector_type=ConnectorType.email,
        metadata_={"imap": {"host": "imap.example.com"}},
        auth_config={"username": "user@example.com", "password": "secret"},
    )
    db_session.add(config)
    db_session.commit()

    # Create a simple email message
    email_msg = email.message.Message()
    email_msg["From"] = "sender@example.com"
    email_msg["Subject"] = "Test Subject"
    email_msg["Message-ID"] = "<msg123@example.com>"
    email_msg.set_payload("Test body")

    mock_client = MagicMock()
    mock_client.uid.side_effect = [
        ("OK", [b"1"]),  # search returns UID 1
        ("OK", [(b"1 (RFC822 {100}", email_msg.as_bytes())]),  # fetch
    ]

    with patch("imaplib.IMAP4_SSL", return_value=mock_client):
        with patch(
            "app.services.crm.inbox.receive_email_message"
        ) as mock_receive:
            result = email_polling._imap_poll(
                db_session,
                config,
                config.metadata_["imap"],
                config.auth_config,
            )

    assert result == 1
    mock_receive.assert_called_once()


def test_imap_poll_skips_self_sender(db_session):
    """IMAP poll should skip messages sent by the configured mailbox."""
    config = ConnectorConfig(
        name="IMAP Self Sender",
        connector_type=ConnectorType.email,
        metadata_={"imap": {"host": "imap.example.com"}},
        auth_config={"username": "user@example.com", "password": "secret"},
    )
    db_session.add(config)
    db_session.commit()

    email_msg = email.message.Message()
    email_msg["From"] = "user@example.com"
    email_msg["Subject"] = "Sent by self"
    email_msg["Message-ID"] = "<self@example.com>"
    email_msg.set_payload("Self body")

    mock_client = MagicMock()
    mock_client.uid.side_effect = [
        ("OK", [b"1"]),
        ("OK", [(b"1 (RFC822 {100}", email_msg.as_bytes())]),
    ]

    with patch("imaplib.IMAP4_SSL", return_value=mock_client):
        with patch("app.services.crm.inbox.receive_email_message") as mock_receive:
            result = email_polling._imap_poll(
                db_session,
                config,
                config.metadata_["imap"],
                config.auth_config,
            )

    assert result == 0
    mock_receive.assert_not_called()


def test_imap_poll_non_ssl(db_session):
    """Test IMAP poll without SSL."""
    config = ConnectorConfig(
        name="IMAP Non-SSL",
        connector_type=ConnectorType.email,
        metadata_={"imap": {"host": "imap.example.com", "use_ssl": False}},
        auth_config={"username": "user@example.com", "password": "secret"},
    )
    db_session.add(config)
    db_session.commit()

    mock_client = MagicMock()
    mock_client.uid.return_value = ("OK", [b""])

    with patch("imaplib.IMAP4", return_value=mock_client):
        result = email_polling._imap_poll(
            db_session,
            config,
            config.metadata_["imap"],
            config.auth_config,
        )

    assert result == 0


def test_imap_poll_with_last_uid(db_session):
    """Test IMAP poll with existing last UID."""
    config = ConnectorConfig(
        name="IMAP With Last UID",
        connector_type=ConnectorType.email,
        metadata_={"imap": {"host": "imap.example.com"}, "imap_last_uid": 100},
        auth_config={"username": "user@example.com", "password": "secret"},
    )
    db_session.add(config)
    db_session.commit()

    mock_client = MagicMock()
    mock_client.uid.return_value = ("OK", [b""])

    with patch("imaplib.IMAP4_SSL", return_value=mock_client):
        result = email_polling._imap_poll(
            db_session,
            config,
            config.metadata_["imap"],
            config.auth_config,
        )

    # Should search for UIDs > 100
    mock_client.uid.assert_called()


# =============================================================================
# POP3 Polling Tests
# =============================================================================


def test_pop3_poll_incomplete_config(db_session):
    """Test POP3 poll with incomplete config raises 400."""
    config = ConnectorConfig(
        name="Incomplete POP3",
        connector_type=ConnectorType.email,
        metadata_={"pop3": {"host": "pop.example.com"}},
        auth_config={},  # Missing username/password
    )
    db_session.add(config)
    db_session.commit()

    with pytest.raises(HTTPException) as exc_info:
        email_polling._pop3_poll(
            db_session,
            config,
            config.metadata_["pop3"],
            config.auth_config,
        )
    assert exc_info.value.status_code == 400
    assert "POP3 config incomplete" in exc_info.value.detail


def test_pop3_poll_success_no_messages(db_session):
    """Test successful POP3 poll with no messages."""
    config = ConnectorConfig(
        name="POP3 Success",
        connector_type=ConnectorType.email,
        metadata_={"pop3": {"host": "pop.example.com", "port": 995}},
        auth_config={"username": "user@example.com", "password": "secret"},
    )
    db_session.add(config)
    db_session.commit()

    mock_client = MagicMock()
    mock_client.uidl.return_value = ("+OK", [], 0)  # No messages

    with patch("poplib.POP3_SSL", return_value=mock_client):
        result = email_polling._pop3_poll(
            db_session,
            config,
            config.metadata_["pop3"],
            config.auth_config,
        )

    assert result == 0
    mock_client.quit.assert_called_once()


def test_pop3_poll_with_messages(db_session):
    """Test POP3 poll processing messages."""
    config = ConnectorConfig(
        name="POP3 With Messages",
        connector_type=ConnectorType.email,
        metadata_={"pop3": {"host": "pop.example.com"}},
        auth_config={"username": "user@example.com", "password": "secret"},
    )
    db_session.add(config)
    db_session.commit()

    # Create a simple email message
    email_msg = email.message.Message()
    email_msg["From"] = "sender@example.com"
    email_msg["Subject"] = "POP3 Test"
    email_msg["Message-ID"] = "<popmsg@example.com>"
    email_msg.set_payload("POP3 body")

    mock_client = MagicMock()
    mock_client.uidl.return_value = ("+OK", [b"1 abc123"], 1)
    mock_client.retr.return_value = (
        "+OK",
        email_msg.as_bytes().split(b"\n"),
        len(email_msg.as_bytes()),
    )

    with patch("poplib.POP3_SSL", return_value=mock_client):
        with patch(
            "app.services.crm.inbox.receive_email_message"
        ) as mock_receive:
            result = email_polling._pop3_poll(
                db_session,
                config,
                config.metadata_["pop3"],
                config.auth_config,
            )

    assert result == 1
    mock_receive.assert_called_once()


def test_pop3_poll_non_ssl(db_session):
    """Test POP3 poll without SSL."""
    config = ConnectorConfig(
        name="POP3 Non-SSL",
        connector_type=ConnectorType.email,
        metadata_={"pop3": {"host": "pop.example.com", "use_ssl": False}},
        auth_config={"username": "user@example.com", "password": "secret"},
    )
    db_session.add(config)
    db_session.commit()

    mock_client = MagicMock()
    mock_client.uidl.return_value = ("+OK", [], 0)

    with patch("poplib.POP3", return_value=mock_client):
        result = email_polling._pop3_poll(
            db_session,
            config,
            config.metadata_["pop3"],
            config.auth_config,
        )

    assert result == 0


def test_pop3_poll_with_last_uidl(db_session):
    """Test POP3 poll skips messages already processed."""
    config = ConnectorConfig(
        name="POP3 With Last UIDL",
        connector_type=ConnectorType.email,
        metadata_={"pop3": {"host": "pop.example.com"}, "pop3_last_uidl": "abc123"},
        auth_config={"username": "user@example.com", "password": "secret"},
    )
    db_session.add(config)
    db_session.commit()

    mock_client = MagicMock()
    # Two messages, first should be skipped (uidl <= last_uidl)
    mock_client.uidl.return_value = ("+OK", [b"1 abc123", b"2 def456"], 2)

    email_msg = email.message.Message()
    email_msg["From"] = "sender@example.com"
    email_msg["Subject"] = "New message"
    email_msg.set_payload("body")

    mock_client.retr.return_value = (
        "+OK",
        email_msg.as_bytes().split(b"\n"),
        len(email_msg.as_bytes()),
    )

    with patch("poplib.POP3_SSL", return_value=mock_client):
        with patch(
            "app.services.crm.inbox.receive_email_message"
        ) as mock_receive:
            result = email_polling._pop3_poll(
                db_session,
                config,
                config.metadata_["pop3"],
                config.auth_config,
            )

    # Only one message should be processed (def456 > abc123)
    assert result == 1


def test_pop3_poll_invalid_uidl_format(db_session):
    """Test POP3 poll handles invalid UIDL format."""
    config = ConnectorConfig(
        name="POP3 Invalid UIDL",
        connector_type=ConnectorType.email,
        metadata_={"pop3": {"host": "pop.example.com"}},
        auth_config={"username": "user@example.com", "password": "secret"},
    )
    db_session.add(config)
    db_session.commit()

    mock_client = MagicMock()
    # Invalid format - should be skipped
    mock_client.uidl.return_value = ("+OK", [b"invalid_format"], 1)

    with patch("poplib.POP3_SSL", return_value=mock_client):
        result = email_polling._pop3_poll(
            db_session,
            config,
            config.metadata_["pop3"],
            config.auth_config,
        )

    assert result == 0  # No messages processed


# =============================================================================
# Main Polling Function Tests
# =============================================================================


def test_poll_email_connector_no_config(db_session):
    """Test polling with no IMAP/POP3 config."""
    config = ConnectorConfig(
        name="No Config",
        connector_type=ConnectorType.email,
        metadata_={},
        auth_config={},
    )
    db_session.add(config)
    db_session.commit()

    result = email_polling.poll_email_connector(db_session, config)

    assert result == {"processed": 0}


def test_poll_email_connector_imap_only(db_session):
    """Test polling with only IMAP config."""
    config = ConnectorConfig(
        name="IMAP Only",
        connector_type=ConnectorType.email,
        metadata_={"imap": {"host": "imap.example.com"}},
        auth_config={"username": "user@example.com", "password": "secret"},
    )
    db_session.add(config)
    db_session.commit()

    mock_client = MagicMock()
    mock_client.uid.return_value = ("OK", [b""])

    with patch("imaplib.IMAP4_SSL", return_value=mock_client):
        result = email_polling.poll_email_connector(db_session, config)

    assert result == {"processed": 0}


def test_poll_email_connector_pop3_only(db_session):
    """Test polling with only POP3 config."""
    config = ConnectorConfig(
        name="POP3 Only",
        connector_type=ConnectorType.email,
        metadata_={"pop3": {"host": "pop.example.com"}},
        auth_config={"username": "user@example.com", "password": "secret"},
    )
    db_session.add(config)
    db_session.commit()

    mock_client = MagicMock()
    mock_client.uidl.return_value = ("+OK", [], 0)

    with patch("poplib.POP3_SSL", return_value=mock_client):
        result = email_polling.poll_email_connector(db_session, config)

    assert result == {"processed": 0}


def test_poll_email_connector_both_protocols(db_session):
    """Test polling with both IMAP and POP3 config."""
    config = ConnectorConfig(
        name="Both Protocols",
        connector_type=ConnectorType.email,
        metadata_={
            "imap": {"host": "imap.example.com"},
            "pop3": {"host": "pop.example.com"},
        },
        auth_config={"username": "user@example.com", "password": "secret"},
    )
    db_session.add(config)
    db_session.commit()

    mock_imap = MagicMock()
    mock_imap.uid.return_value = ("OK", [b""])

    mock_pop3 = MagicMock()
    mock_pop3.uidl.return_value = ("+OK", [], 0)

    with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
        with patch("poplib.POP3_SSL", return_value=mock_pop3):
            result = email_polling.poll_email_connector(db_session, config)

    assert result == {"processed": 0}


def test_poll_email_connector_none_metadata(db_session):
    """Test polling with None metadata."""
    config = ConnectorConfig(
        name="None Metadata",
        connector_type=ConnectorType.email,
        metadata_=None,
        auth_config=None,
    )
    db_session.add(config)
    db_session.commit()

    result = email_polling.poll_email_connector(db_session, config)

    assert result == {"processed": 0}
