from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.crm.conversation import Conversation
from app.models.crm.enums import ChannelType
from app.schemas.crm.inbox import InboxSendRequest
from app.services.crm.inbox import outbox


def _create_conversation(db_session, person):
    conversation = Conversation(person_id=person.id)
    db_session.add(conversation)
    db_session.commit()
    db_session.refresh(conversation)
    return conversation


def test_enqueue_outbox_idempotency(db_session, person, monkeypatch):
    conversation = _create_conversation(db_session, person)
    payload = InboxSendRequest(
        conversation_id=conversation.id,
        channel_type=ChannelType.email,
        body="Hello",
    )
    first = outbox.enqueue_outbound_message(
        db_session,
        payload=payload,
        author_id=None,
        idempotency_key="dedupe-key",
        dispatch=False,
    )
    second = outbox.enqueue_outbound_message(
        db_session,
        payload=payload,
        author_id=None,
        idempotency_key="dedupe-key",
        dispatch=False,
    )
    assert first.id == second.id


def test_process_outbox_item_success(db_session, person, monkeypatch):
    conversation = _create_conversation(db_session, person)
    payload = InboxSendRequest(
        conversation_id=conversation.id,
        channel_type=ChannelType.email,
        body="Hello",
    )
    queued = outbox.enqueue_outbound_message(
        db_session,
        payload=payload,
        author_id=None,
        dispatch=False,
    )

    message_id = uuid4()
    monkeypatch.setattr(
        outbox,
        "send_message_with_retry",
        lambda *args, **kwargs: SimpleNamespace(id=message_id),
    )

    processed = outbox.process_outbox_item(db_session, str(queued.id))
    assert processed.status == outbox.STATUS_SENT
    assert processed.message_id == message_id


def test_process_outbox_item_transient_error(db_session, person, monkeypatch):
    conversation = _create_conversation(db_session, person)
    payload = InboxSendRequest(
        conversation_id=conversation.id,
        channel_type=ChannelType.email,
        body="Hello",
    )
    queued = outbox.enqueue_outbound_message(
        db_session,
        payload=payload,
        author_id=None,
        dispatch=False,
    )

    def _raise(*args, **kwargs):
        raise outbox.TransientOutboundError("temporary")

    monkeypatch.setattr(outbox, "send_message_with_retry", _raise)
    with pytest.raises(outbox.TransientOutboundError):
        outbox.process_outbox_item(db_session, str(queued.id))

    refreshed = db_session.get(type(queued), queued.id)
    assert refreshed.status == outbox.STATUS_RETRYING
    assert refreshed.next_attempt_at is not None


def test_list_due_outbox_ids(db_session, person):
    conversation = _create_conversation(db_session, person)
    payload = InboxSendRequest(
        conversation_id=conversation.id,
        channel_type=ChannelType.email,
        body="Hello",
    )
    queued = outbox.enqueue_outbound_message(
        db_session,
        payload=payload,
        author_id=None,
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
        dispatch=False,
    )
    due = outbox.list_due_outbox_ids(db_session, limit=10)
    assert str(queued.id) in due
