import uuid

from app.services.crm.inbox.attachments import fetch_inbox_attachment


class _FakeDB:
    def __init__(self):
        self.pk = None

    def get(self, _model, pk):
        self.pk = pk
        return None


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
