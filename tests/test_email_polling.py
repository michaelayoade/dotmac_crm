from app.models.connector import ConnectorConfig, ConnectorType
from app.services.crm.inbox import email_polling


class _FakeDb:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


class _FakeImapClient:
    def __init__(self, messages: dict[str, bytes]):
        self.messages = messages

    def login(self, username, password):
        return "OK", []

    def select(self, mailbox):
        return "OK", [b"2"]

    def uid(self, command, *args):
        if command == "search":
            return "OK", [b" ".join(uid.encode("ascii") for uid in self.messages)]
        if command == "fetch":
            uid = str(args[0])
            return "OK", [(b"RFC822", self.messages[uid])]
        raise AssertionError(f"unexpected uid command: {command}")


class _FakePop3Client:
    def __init__(self, messages: dict[str, bytes]):
        self.messages = messages

    def uidl(self):
        listings = [f"{index} {uidl}".encode("ascii") for index, uidl in enumerate(self.messages, start=1)]
        return "+OK", listings, 0

    def retr(self, msg_num: int):
        uidl = list(self.messages)[msg_num - 1]
        return "+OK", self.messages[uidl].splitlines(), 0


def _message(*, from_header: str | None, subject: str, message_id: str, body: str = "Body") -> bytes:
    headers = [
        f"Message-ID: {message_id}",
        f"Subject: {subject}",
        "Reply-To: fallback@example.com",
        "Return-Path: <bounce@example.com>",
    ]
    if from_header is not None:
        headers.insert(0, f"From: {from_header}")
    return ("\r\n".join(headers) + "\r\n\r\n" + body).encode("utf-8")


def test_imap_poll_skips_blank_sender_and_continues(monkeypatch):
    db = _FakeDb()
    config = ConnectorConfig(
        name="Sales Mail",
        connector_type=ConnectorType.email,
        metadata_={},
        auth_config={"username": "sales@example.com", "password": "secret"},
    )
    client = _FakeImapClient(
        {
            "10": _message(from_header=None, subject="Blank Sender", message_id="<bad@example.com>"),
            "11": _message(
                from_header="Customer <customer@example.com>", subject="Valid", message_id="<ok@example.com>"
            ),
        }
    )
    received = []

    def _receive_email_message(_db, payload):
        received.append(payload)
        return None

    monkeypatch.setattr(email_polling.inbox_service, "receive_email_message", _receive_email_message)

    processed = email_polling._imap_poll_inner(
        db,
        config,
        {"mailbox": "INBOX"},
        config.auth_config,
        "8c86d010-5f7f-4dab-9a95-b368ca3ed35e",
        client,
    )

    assert processed == 0
    assert [payload.contact_address for payload in received] == ["customer@example.com"]
    assert config.metadata_["imap_last_uid"] == 11
    skip = config.metadata_["malformed_email_skips"][0]
    assert skip["protocol"] == "imap"
    assert skip["uid"] == "10"
    assert skip["subject"] == "Blank Sender"
    assert skip["reply_to"] == ["fallback@example.com"]
    assert skip["return_path"] == "<bounce@example.com>"
    assert db.commits == 1


def test_pop3_poll_skips_blank_sender_and_continues(monkeypatch):
    db = _FakeDb()
    metadata: dict = {}
    seen_uidls_raw: list = []
    config = ConnectorConfig(
        name="Sales Mail POP3",
        connector_type=ConnectorType.email,
        metadata_=metadata,
        auth_config={"username": "sales@example.com", "password": "secret"},
    )
    client = _FakePop3Client(
        {
            "UID1": _message(from_header=None, subject="Blank Sender", message_id="<bad-pop@example.com>"),
            "UID2": _message(
                from_header="Customer <customer@example.com>",
                subject="Valid",
                message_id="<ok-pop@example.com>",
            ),
        }
    )
    received = []

    def _receive_email_message(_db, payload):
        received.append(payload)
        return None

    monkeypatch.setattr(email_polling.inbox_service, "receive_email_message", _receive_email_message)

    processed = email_polling._pop3_poll_inner(
        db,
        config,
        {},
        config.auth_config,
        "8c86d010-5f7f-4dab-9a95-b368ca3ed35e",
        client,
        metadata,
        None,
        set(),
        seen_uidls_raw,
    )

    assert processed == 0
    assert [payload.contact_address for payload in received] == ["customer@example.com"]
    assert config.metadata_["pop3_last_uidl"] == "UID2"
    assert config.metadata_["pop3_seen_uidls"] == ["UID1", "UID2"]
    skip = config.metadata_["malformed_email_skips"][0]
    assert skip["protocol"] == "pop3"
    assert skip["uidl"] == "UID1"
    assert skip["subject"] == "Blank Sender"
    assert db.commits == 1


def test_imap_poll_stops_at_message_cap_and_records_run(monkeypatch):
    db = _FakeDb()
    config = ConnectorConfig(
        name="Sales Mail",
        connector_type=ConnectorType.email,
        metadata_={"poll_max_messages": 1},
        auth_config={"username": "sales@example.com", "password": "secret"},
    )
    client = _FakeImapClient(
        {
            "10": _message(from_header="One <one@example.com>", subject="One", message_id="<one@example.com>"),
            "11": _message(from_header="Two <two@example.com>", subject="Two", message_id="<two@example.com>"),
        }
    )
    received = []

    def _receive_email_message(_db, payload):
        received.append(payload)
        return None

    monkeypatch.setattr(email_polling.inbox_service, "receive_email_message", _receive_email_message)

    processed = email_polling._imap_poll_inner(
        db,
        config,
        {"mailbox": "INBOX"},
        config.auth_config,
        "8c86d010-5f7f-4dab-9a95-b368ca3ed35e",
        client,
    )

    assert processed == 0
    assert [payload.contact_address for payload in received] == ["one@example.com"]
    assert config.metadata_["imap_last_uid"] == 10
    assert config.metadata_["last_email_poll"]["attempted"] == 1
    assert config.metadata_["last_email_poll"]["limited_reason"] == "max_messages"
    assert db.commits == 1


def test_pop3_poll_stops_at_message_cap_and_records_run(monkeypatch):
    db = _FakeDb()
    metadata = {"poll_max_messages": 1}
    seen_uidls_raw: list = []
    config = ConnectorConfig(
        name="Sales Mail POP3",
        connector_type=ConnectorType.email,
        metadata_=metadata,
        auth_config={"username": "sales@example.com", "password": "secret"},
    )
    client = _FakePop3Client(
        {
            "UID1": _message(from_header="One <one@example.com>", subject="One", message_id="<one-pop@example.com>"),
            "UID2": _message(from_header="Two <two@example.com>", subject="Two", message_id="<two-pop@example.com>"),
        }
    )
    received = []

    def _receive_email_message(_db, payload):
        received.append(payload)
        return None

    monkeypatch.setattr(email_polling.inbox_service, "receive_email_message", _receive_email_message)

    processed = email_polling._pop3_poll_inner(
        db,
        config,
        {},
        config.auth_config,
        "8c86d010-5f7f-4dab-9a95-b368ca3ed35e",
        client,
        metadata,
        None,
        set(),
        seen_uidls_raw,
    )

    assert processed == 0
    assert [payload.contact_address for payload in received] == ["one@example.com"]
    assert config.metadata_["pop3_last_uidl"] == "UID1"
    assert config.metadata_["pop3_seen_uidls"] == ["UID1"]
    assert config.metadata_["last_email_poll"]["attempted"] == 1
    assert config.metadata_["last_email_poll"]["limited_reason"] == "max_messages"
    assert db.commits == 1
