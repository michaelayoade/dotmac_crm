from __future__ import annotations

from types import SimpleNamespace

from app.services.crm.inbox import email_polling


def _raw_email(*, from_header: str | None, message_id: str, subject: str = "Hello") -> bytes:
    headers = [
        f"Message-ID: {message_id}",
        f"Subject: {subject}",
        "Date: Sat, 18 Jul 2026 09:00:00 +0000",
    ]
    if from_header is not None:
        headers.append(f"From: {from_header}")
    headers.append("To: sales@example.com")
    return ("\r\n".join(headers) + "\r\n\r\nBody").encode()


class FakeDb:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


class FakeImapClient:
    def __init__(self, uid: bytes, raw: bytes):
        self.uid_value = uid
        self.raw = raw
        self.searches: list[str] = []

    def login(self, username, password):
        return "OK", []

    def select(self, mailbox):
        return "OK", [b"1"]

    def uid(self, command, *args):
        if command == "search":
            criteria = args[-1]
            self.searches.append(criteria)
            return "OK", [self.uid_value]
        if command == "fetch":
            return "OK", [(b"1 (UID " + self.uid_value + b" RFC822 {1}", self.raw)]
        raise AssertionError(f"Unexpected IMAP command: {command}")


def test_imap_poll_skips_malformed_sender_and_advances_cursor(monkeypatch):
    db = FakeDb()
    config = SimpleNamespace(metadata_={"imap_last_uid": "5"}, auth_config={})
    client = FakeImapClient(
        b"6",
        _raw_email(from_header=None, message_id="<missing-sender@example.com>", subject="Bad sender"),
    )

    received = []
    monkeypatch.setattr(email_polling.inbox_service, "receive_email_message", lambda *_args: received.append(True))

    processed = email_polling._imap_poll_inner(
        db,
        config,
        {"mailbox": "INBOX"},
        {"username": "sales@example.com", "password": "secret"},
        None,
        client,
    )

    assert processed == 0
    assert received == []
    assert config.metadata_["imap_last_uid"] == 6
    assert config.metadata_["last_email_poll"]["malformed_skips"] == 1
    assert config.metadata_["malformed_email_skips"][-1]["uid"] == "6"
    assert client.searches == ["(UID 6:*)"]
    assert db.commits == 1


def test_imap_poll_advances_cursor_for_duplicate_message(monkeypatch):
    db = FakeDb()
    config = SimpleNamespace(metadata_={"imap_last_uid": 10}, auth_config={})
    client = FakeImapClient(
        b"11",
        _raw_email(from_header="Sender <sender@example.com>", message_id="<duplicate@example.com>"),
    )

    existing_message = SimpleNamespace(id="existing-message-id")
    monkeypatch.setattr(email_polling.inbox_service, "receive_email_message", lambda *_args: existing_message)

    processed = email_polling._imap_poll_inner(
        db,
        config,
        {"mailbox": "INBOX"},
        {"username": "sales@example.com", "password": "secret"},
        None,
        client,
    )

    assert processed == 1
    assert config.metadata_["imap_last_uid"] == 11
    assert config.metadata_["last_email_poll"]["attempted"] == 1
    assert config.metadata_["last_email_poll"]["malformed_skips"] == 0
    assert client.searches == ["(UID 11:*)"]
    assert db.commits == 1
