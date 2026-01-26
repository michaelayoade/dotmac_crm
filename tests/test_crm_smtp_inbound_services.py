from email.message import EmailMessage

from unittest.mock import patch

from app.services.crm import smtp_inbound


def test_smtp_inbound_skips_self_sender():
    """SMTP inbound should ignore messages from configured self addresses."""
    msg = EmailMessage()
    msg["From"] = "self@example.com"
    msg["To"] = "self@example.com"
    msg["Subject"] = "Self"
    msg.set_content("Self body")

    with patch("app.services.crm.inbox.receive_email_message") as mock_receive:
        smtp_inbound._handle_message(
            "self@example.com",
            ["self@example.com"],
            msg.as_bytes(),
            {"self@example.com"},
        )

    mock_receive.assert_not_called()
