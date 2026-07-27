from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType
from app.models.person import Person
from app.models.subscriber import Subscriber
from app.services.crm.inbox.selfcare_history_import import (
    HistoryConversation,
    HistoryExport,
    HistoryImportError,
    HistoryMessage,
    import_history,
)


def _history(source_subscriber_id):
    now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
    return HistoryExport(
        schema="dotmac_sub.native_chat_history.v1",
        exported_at=now,
        content_sha256="a" * 64,
        conversation_count=1,
        message_count=1,
        conversations=[
            HistoryConversation(
                source_conversation_id=uuid4(),
                source_subscriber_id=source_subscriber_id,
                subject="Chat with customer",
                created_at=now,
                first_message_at=now,
                last_message_at=now,
                metadata={"surface": "customer"},
                messages=[
                    HistoryMessage(
                        source_message_id=uuid4(),
                        client_message_id="client-1",
                        body="Please help",
                        received_at=now,
                        created_at=now,
                    )
                ],
            )
        ],
    )


def test_import_is_timestamp_preserving_and_idempotent(db_session):
    source_subscriber_id = uuid4()
    person = Person(
        first_name="Import",
        last_name="Customer",
        email=f"{uuid4()}@example.com",
        metadata_={"selfcare_id": str(source_subscriber_id)},
    )
    db_session.add(person)
    db_session.flush()
    db_session.add(
        Subscriber(
            person_id=person.id,
            external_system="selfcare",
            external_id=str(source_subscriber_id),
        )
    )
    db_session.commit()
    history = _history(source_subscriber_id)

    dry_run = import_history(db_session, history, apply=False)
    assert dry_run.status == "dry_run"
    assert dry_run.conversations_created == 1
    assert db_session.query(Conversation).count() == 0

    applied = import_history(db_session, history, apply=True)
    assert applied.conversations_created == 1
    assert applied.messages_created == 1
    conversation = db_session.query(Conversation).one()
    message = db_session.query(Message).one()
    assert conversation.created_at.replace(tzinfo=UTC) == history.conversations[0].created_at
    assert message.received_at.replace(tzinfo=UTC) == history.conversations[0].messages[0].received_at
    assert message.channel_type == ChannelType.chat_widget
    assert message.metadata_["suppress_live_automation"] is True

    replay = import_history(db_session, history, apply=True)
    assert replay.conversations_reused == 1
    assert replay.messages_reused == 1
    assert db_session.query(Conversation).count() == 1
    assert db_session.query(Message).count() == 1


def test_import_fails_closed_for_unmapped_subscriber(db_session):
    history = _history(uuid4())

    with pytest.raises(HistoryImportError, match="exactly one"):
        import_history(db_session, history, apply=False)


def test_import_rejects_duplicate_source_message_ids(db_session):
    source_subscriber_id = uuid4()
    person = Person(
        first_name="Import",
        last_name="Customer",
        email=f"{uuid4()}@example.com",
        metadata_={"selfcare_id": str(source_subscriber_id)},
    )
    db_session.add(person)
    db_session.commit()
    history = _history(source_subscriber_id)
    duplicate = history.conversations[0].model_copy(update={"source_conversation_id": uuid4()})
    history = history.model_copy(
        update={
            "conversation_count": 2,
            "message_count": 2,
            "conversations": [history.conversations[0], duplicate],
        }
    )

    with pytest.raises(HistoryImportError, match="Duplicate source message"):
        import_history(db_session, history, apply=False)
