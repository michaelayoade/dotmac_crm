from app.models.crm.enums import ChannelType
from app.schemas.crm.inbox import InboxSendRequest
from app.services.email import _build_email_message


def test_inbox_send_request_accepts_bcc_addresses():
    payload = InboxSendRequest(
        conversation_id="00000000-0000-0000-0000-000000000001",
        channel_type=ChannelType.email,
        body="Hello",
        bcc_addresses=["audit@example.com"],
    )
    assert payload.bcc_addresses == ["audit@example.com"]


def test_email_message_does_not_expose_bcc_header():
    message = _build_email_message(
        subject="Subject",
        from_name="Support",
        from_email="support@example.com",
        to_email="to@example.com",
        cc_emails=["cc@example.com"],
        bcc_emails=["bcc@example.com"],
        body_html="<p>Hello</p>",
        body_text="Hello",
    )
    assert message["To"] == "to@example.com"
    assert message["Cc"] == "cc@example.com"
    assert message.get("Bcc") is None
