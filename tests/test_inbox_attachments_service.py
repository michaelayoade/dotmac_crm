import uuid

from app.models.crm.conversation import Message, MessageAttachment
from app.models.crm.enums import ChannelType, MessageDirection, MessageStatus
from app.services.crm.inbox.attachments import fetch_inbox_attachment, fetch_stored_message_attachment
from app.services.crm.inbox.formatting import format_message_for_template


class _FakeDB:
    def __init__(self):
        self.pk = None
        self.records = {}

    def get(self, model, pk):
        self.pk = pk
        return self.records.get((model, pk))


def test_fetch_inbox_attachment_coerces_message_id_to_uuid():
    db = _FakeDB()
    message_id = str(uuid.uuid4())

    result = fetch_inbox_attachment(db, message_id, 0)

    assert result.kind == "not_found"
    assert isinstance(db.pk, uuid.UUID)


def test_fetch_inbox_attachment_rejects_invalid_message_id():
    db = _FakeDB()

    result = fetch_inbox_attachment(db, "not-a-uuid", 0)

    assert result.kind == "not_found"
    assert db.pk is None


def test_fetch_stored_message_attachment_returns_decoded_content():
    db = _FakeDB()
    attachment_id = uuid.uuid4()
    db.records[(MessageAttachment, attachment_id)] = MessageAttachment(
        id=attachment_id,
        message_id=uuid.uuid4(),
        file_name="Day 20.png",
        mime_type="image/png",
        file_size=8,
        metadata_={"content_base64": "iVBORw0KGgo="},
    )

    result = fetch_stored_message_attachment(db, str(attachment_id))

    assert result.kind == "content"
    assert result.content == b"\x89PNG\r\n\x1a\n"
    assert result.content_type == "image/png"
    assert result.file_name == "Day 20.png"


def test_format_message_for_template_uses_attachment_route_instead_of_data_url(monkeypatch):
    monkeypatch.setattr(
        "app.services.crm.inbox.formatting.time_preferences.resolve_company_time_prefs",
        lambda _db: ("UTC", "%Y-%m-%d", "%H:%M", None),
    )
    attachment_id = uuid.uuid4()
    message = Message(
        id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        channel_type=ChannelType.email,
        direction=MessageDirection.inbound,
        status=MessageStatus.received,
        body="Attachment test",
    )
    message.attachments = [
        MessageAttachment(
            id=attachment_id,
            message_id=message.id,
            file_name="Day 20.png",
            mime_type="image/png",
            file_size=8,
            metadata_={"content_base64": "iVBORw0KGgo="},
        )
    ]

    payload = format_message_for_template(message, _FakeDB())

    assert payload["attachments"][0]["url"] == f"/admin/crm/inbox/message-attachment/{attachment_id}"
