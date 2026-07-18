from unittest.mock import MagicMock, patch

from app.services.email import send_email_with_config


def test_send_email_with_config_uses_bounded_timeout():
    server = MagicMock()
    server.sendmail.return_value = {}

    with patch("app.services.email._create_smtp_client", return_value=server) as create_client:
        sent, debug = send_email_with_config(
            {
                "host": "smtp.example.com",
                "port": 465,
                "use_ssl": True,
                "from_email": "sender@example.com",
                "timeout_sec": 7,
            },
            "recipient@example.com",
            "Subject",
            "<p>Body</p>",
            "Body",
        )

    assert sent is True
    assert debug is None
    create_client.assert_called_once_with("smtp.example.com", 465, True, timeout=7.0)
