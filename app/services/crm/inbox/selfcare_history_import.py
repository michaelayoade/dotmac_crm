"""Idempotent historical import for native chats created in Dotmac Sub."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import (
    ChannelType,
    ConversationStatus,
    MessageDirection,
    MessageStatus,
)
from app.models.person import Person
from app.models.subscriber import Subscriber
from app.services.crm.inbox.summaries import recompute_conversation_summary

SOURCE_SYSTEM = "dotmac_sub"
SOURCE_SCHEMA = "dotmac_sub.native_chat_history.v1"


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_message_id: UUID
    client_message_id: str | None = None
    body: str
    received_at: datetime
    created_at: datetime


class HistoryConversation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_conversation_id: UUID
    source_subscriber_id: UUID
    subject: str | None = Field(default=None, max_length=200)
    created_at: datetime
    first_message_at: datetime | None = None
    last_message_at: datetime | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    messages: list[HistoryMessage] = Field(min_length=1)


class HistoryExport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_name: str = Field(alias="schema")
    exported_at: datetime
    content_sha256: str = Field(min_length=64, max_length=64)
    conversation_count: int = Field(ge=0)
    message_count: int = Field(ge=0)
    conversations: list[HistoryConversation]


@dataclass(frozen=True)
class HistoryImportResult:
    status: str
    conversations_created: int
    conversations_reused: int
    messages_created: int
    messages_reused: int


class HistoryImportError(RuntimeError):
    pass


def _message_external_id(source_message_id: UUID) -> str:
    return f"dotmac_sub:{source_message_id}"


def _conversation_fingerprint(source: HistoryConversation) -> str:
    stable = {
        "source_conversation_id": str(source.source_conversation_id),
        "source_subscriber_id": str(source.source_subscriber_id),
        "subject": source.subject,
        "created_at": source.created_at.astimezone(UTC).isoformat(),
    }
    canonical = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def _resolve_person(db: Session, source_subscriber_id: UUID) -> Person:
    source_id = str(source_subscriber_id)
    candidates: dict[UUID, Person] = {}
    subscribers = (
        db.query(Subscriber).filter(Subscriber.external_id == source_id).filter(Subscriber.is_active.is_(True)).all()
    )
    for subscriber in subscribers:
        if subscriber.person_id:
            person = db.get(Person, subscriber.person_id)
            if person and person.is_active:
                candidates[person.id] = person
    people = (
        db.query(Person)
        .filter(Person.metadata_["selfcare_id"].as_string() == source_id)
        .filter(Person.is_active.is_(True))
        .all()
    )
    candidates.update({person.id: person for person in people})
    if len(candidates) != 1:
        raise HistoryImportError("Source subscriber must resolve to exactly one active CRM person.")
    return next(iter(candidates.values()))


def _source_conversation(db: Session, source_conversation_id: UUID) -> Conversation | None:
    rows = (
        db.query(Conversation)
        .filter(
            Conversation.metadata_["source_system"].as_string() == SOURCE_SYSTEM,
            Conversation.metadata_["source_conversation_id"].as_string() == str(source_conversation_id),
        )
        .all()
    )
    if len(rows) > 1:
        raise HistoryImportError("Duplicate imported source conversation detected.")
    return rows[0] if rows else None


def _existing_message(db: Session, source_message_id: UUID) -> Message | None:
    return (
        db.query(Message)
        .filter(Message.channel_type == ChannelType.chat_widget)
        .filter(Message.external_id == _message_external_id(source_message_id))
        .one_or_none()
    )


def _verify_existing_message(
    message: Message,
    *,
    conversation: Conversation,
    source: HistoryMessage,
) -> None:
    if message.conversation_id != conversation.id:
        raise HistoryImportError("Imported source message belongs to another thread.")
    if (message.body or "") != source.body or not _same_instant(message.received_at, source.received_at):
        raise HistoryImportError("Imported source message fingerprint changed.")


def _same_instant(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return left is right
    left_aware = left if left.tzinfo else left.replace(tzinfo=UTC)
    right_aware = right if right.tzinfo else right.replace(tzinfo=UTC)
    return left_aware.astimezone(UTC) == right_aware.astimezone(UTC)


def _lock_import(db: Session) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"{SOURCE_SYSTEM}:native-chat-history"},
        )


def _validate_payload(history: HistoryExport) -> None:
    if history.schema_name != SOURCE_SCHEMA:
        raise HistoryImportError("Unsupported Selfcare history schema.")
    if history.conversation_count != len(history.conversations):
        raise HistoryImportError("Conversation count does not match export payload.")
    if history.message_count != sum(len(conversation.messages) for conversation in history.conversations):
        raise HistoryImportError("Message count does not match export payload.")
    conversation_ids = [conversation.source_conversation_id for conversation in history.conversations]
    if len(conversation_ids) != len(set(conversation_ids)):
        raise HistoryImportError("Duplicate source conversation in export payload.")
    message_ids = [
        message.source_message_id for conversation in history.conversations for message in conversation.messages
    ]
    if len(message_ids) != len(set(message_ids)):
        raise HistoryImportError("Duplicate source message in export payload.")


def import_history(
    db: Session,
    history: HistoryExport,
    *,
    apply: bool,
) -> HistoryImportResult:
    _validate_payload(history)
    if apply:
        _lock_import(db)

    conversations_created = 0
    conversations_reused = 0
    messages_created = 0
    messages_reused = 0
    touched: list[Conversation] = []

    for source_conversation in history.conversations:
        person = _resolve_person(db, source_conversation.source_subscriber_id)
        conversation = _source_conversation(db, source_conversation.source_conversation_id)
        source_fingerprint = _conversation_fingerprint(source_conversation)
        if conversation is not None:
            if conversation.person_id != person.id:
                raise HistoryImportError("Imported source conversation resolved to a different person.")
            if (conversation.metadata_ or {}).get("source_conversation_sha256") != source_fingerprint:
                raise HistoryImportError("Imported source conversation fingerprint changed.")
            conversations_reused += 1
        elif apply:
            metadata = {
                **source_conversation.metadata,
                "source_system": SOURCE_SYSTEM,
                "source_schema": SOURCE_SCHEMA,
                "source_conversation_id": str(source_conversation.source_conversation_id),
                "source_subscriber_id": str(source_conversation.source_subscriber_id),
                "source_conversation_sha256": source_fingerprint,
                "source_export_sha256": history.content_sha256,
                "historical_import": True,
                "live_reply_transport": False,
            }
            conversation = Conversation(
                person_id=person.id,
                status=ConversationStatus.open,
                subject=source_conversation.subject or "Imported Selfcare chat",
                last_message_at=source_conversation.last_message_at,
                metadata_=metadata,
                created_at=source_conversation.created_at,
            )
            db.add(conversation)
            db.flush()
            conversations_created += 1
        else:
            conversations_created += 1

        for source_message in source_conversation.messages:
            existing = _existing_message(db, source_message.source_message_id)
            if existing is not None:
                if conversation is None:
                    raise HistoryImportError("Source message exists without its imported conversation.")
                _verify_existing_message(
                    existing,
                    conversation=conversation,
                    source=source_message,
                )
                messages_reused += 1
                continue
            if apply:
                if conversation is None:
                    raise HistoryImportError("Import conversation was not materialized.")
                db.add(
                    Message(
                        conversation_id=conversation.id,
                        channel_type=ChannelType.chat_widget,
                        direction=MessageDirection.inbound,
                        status=MessageStatus.received,
                        body=source_message.body,
                        external_id=_message_external_id(source_message.source_message_id),
                        external_ref=source_message.client_message_id,
                        received_at=source_message.received_at,
                        created_at=source_message.created_at,
                        metadata_={
                            "source_system": SOURCE_SYSTEM,
                            "source_schema": SOURCE_SCHEMA,
                            "source_message_id": str(source_message.source_message_id),
                            "source_conversation_id": str(source_conversation.source_conversation_id),
                            "historical_import": True,
                            "suppress_live_automation": True,
                        },
                    )
                )
            messages_created += 1
        if conversation is not None:
            touched.append(conversation)

    if apply:
        db.flush()
        for conversation in touched:
            recompute_conversation_summary(db, str(conversation.id))
        db.commit()
        from app.services.crm.inbox import cache as inbox_cache

        inbox_cache.invalidate_inbox_list()
    return HistoryImportResult(
        status="applied" if apply else "dry_run",
        conversations_created=conversations_created,
        conversations_reused=conversations_reused,
        messages_created=messages_created,
        messages_reused=messages_reused,
    )
