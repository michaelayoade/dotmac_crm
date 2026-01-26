from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import re
import uuid
from email.utils import getaddresses

import httpx
from fastapi import HTTPException
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session
from sqlalchemy.orm import selectinload

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.domain_settings import SettingDomain
from app.models.integration import (
    IntegrationJob,
    IntegrationJobType,
    IntegrationScheduleType,
    IntegrationTarget,
    IntegrationTargetType,
)
from app.models.person import ChannelType as PersonChannelType, Person, PersonChannel
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.models.subscriber import AccountRole, AccountStatus, Subscriber, SubscriberAccount
from app.schemas.crm.conversation import ConversationCreate, MessageCreate
from app.schemas.crm.inbox import EmailWebhookPayload, InboxSendRequest, WhatsAppWebhookPayload
from app.services import email as email_service
from app.services.common import coerce_uuid
from app.services.crm import contact as contact_service
from app.services.crm import conversation as conversation_service
from app.services.crm import email_polling
from app.logging import get_logger
from app.services.settings_spec import resolve_value

_DEFAULT_WHATSAPP_TIMEOUT = 10  # fallback when settings unavailable

logger = get_logger(__name__)


def _get_whatsapp_api_timeout(db: Session) -> int:
    """Get the WhatsApp API timeout from settings."""
    timeout = resolve_value(db, SettingDomain.comms, "whatsapp_api_timeout_seconds")
    return timeout if timeout else _DEFAULT_WHATSAPP_TIMEOUT


def _now():
    return datetime.now(timezone.utc)


def _normalize_external_id(raw_id: str | None) -> str | None:
    if not raw_id:
        return None
    candidate = raw_id.strip()
    if not candidate:
        return None
    if len(candidate) > 120:
        return hashlib.sha256(candidate.encode("utf-8")).hexdigest()
    return candidate


def _normalize_email_message_id(raw_id: str | None) -> str | None:
    if not raw_id:
        return None
    cleaned = raw_id.strip().strip("<>").strip()
    return _normalize_external_id(cleaned)


def _get_metadata_value(metadata: dict | None, key: str):
    if not metadata:
        return None
    if key in metadata:
        return metadata[key]
    lower_key = key.lower()
    for meta_key, value in metadata.items():
        if isinstance(meta_key, str) and meta_key.lower() == lower_key:
            return value
    headers = metadata.get("headers") if isinstance(metadata.get("headers"), dict) else None
    if headers:
        for header_key, value in headers.items():
            if isinstance(header_key, str) and header_key.lower() == lower_key:
                return value
    return None


def _extract_message_ids(value) -> list[str]:
    if not value:
        return []
    candidates: list[str] = []
    if isinstance(value, (list, tuple, set)):
        for item in value:
            if item:
                candidates.append(str(item))
    else:
        candidates.append(str(value))
    message_ids: list[str] = []
    for raw in candidates:
        for token in re.findall(r"<[^>]+>|[^\\s]+", raw):
            stripped = _normalize_email_message_id(token)
            if stripped:
                message_ids.append(stripped)
            raw_norm = _normalize_external_id(token.strip())
            if raw_norm and raw_norm not in message_ids:
                message_ids.append(raw_norm)
    return message_ids


def _extract_conversation_tokens(text: str | None) -> list[str]:
    if not text:
        return []
    tokens = []
    conv_matches = re.findall(r"(?:conv[_-]?|conversation[_-]?)([0-9a-fA-F-]{8,36})", text)
    ticket_matches = re.findall(r"(?:ticket\\s*#\\s*)([0-9a-fA-F-]{8,36})", text, flags=re.IGNORECASE)
    tokens.extend(conv_matches)
    tokens.extend(ticket_matches)
    return tokens


def _find_conversation_by_token(db: Session, token: str) -> Conversation | None:
    cleaned = token.strip().strip("[]()")
    if not cleaned:
        return None
    lowered = cleaned.lower()
    try:
        if len(lowered) == 32:
            return db.get(Conversation, uuid.UUID(hex=lowered))
        if len(lowered) == 36:
            return db.get(Conversation, uuid.UUID(lowered))
    except (ValueError, AttributeError):
        return None
    if len(lowered) >= 8 and len(lowered) < 32 and re.fullmatch(r"[0-9a-f]+", lowered):
        return (
            db.query(Conversation)
            .filter(func.lower(cast(Conversation.id, String)).like(f"{lowered}%"))
            .order_by(Conversation.created_at.desc())
            .first()
        )
    if re.fullmatch(r"[0-9]+", lowered) and len(lowered) >= 4:
        return (
            db.query(Conversation)
            .filter(Conversation.subject.ilike(f"%{lowered}%"))
            .order_by(Conversation.updated_at.desc())
            .first()
        )
    return None


def _resolve_conversation_from_email_metadata(
    db: Session,
    subject: str | None,
    metadata: dict | None,
) -> Conversation | None:
    address_fields = []
    for key in ("reply_to", "to", "cc"):
        value = _get_metadata_value(metadata, key)
        if value:
            address_fields.append(value)
    addresses = []
    for raw in address_fields:
        if isinstance(raw, (list, tuple, set)):
            raw_values = [str(item) for item in raw if item]
        else:
            raw_values = [str(raw)]
        for _, addr in getaddresses(raw_values):
            if addr:
                addresses.append(addr)
    tokens = _extract_conversation_tokens(subject)
    for address in addresses:
        tokens.extend(_extract_conversation_tokens(address))
    for token in tokens:
        conv = _find_conversation_by_token(db, token)
        if conv:
            return conv
    in_reply_to = _get_metadata_value(metadata, "in_reply_to")
    references = _get_metadata_value(metadata, "references")
    for msg_id in _extract_message_ids(in_reply_to) + _extract_message_ids(references):
        match = (
            db.query(Message)
            .filter(Message.channel_type == ChannelType.email)
            .filter(Message.external_id == msg_id)
            .order_by(Message.created_at.desc())
            .first()
        )
        if match:
            return match.conversation
    return None


def _build_inbound_dedupe_id(
    channel_type: ChannelType,
    contact_address: str,
    subject: str | None,
    body: str | None,
    received_at: datetime | None,
    source_id: str | None = None,
) -> str:
    address = contact_address.strip()
    if channel_type == ChannelType.email:
        address = address.lower()
    received_at_str = ""
    if received_at:
        received_at_str = received_at.replace(microsecond=0).isoformat()
    key = "|".join(
        [
            channel_type.value,
            source_id or "",
            address,
            subject or "",
            body or "",
            received_at_str,
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _render_personalization(body: str, personalization: dict | None) -> str:
    if not personalization:
        return body
    rendered = body
    for key, value in personalization.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
    return rendered


def _set_message_send_error(
    message: Message,
    channel: str,
    error: str,
    status_code: int | None = None,
    response_text: str | None = None,
) -> None:
    metadata = message.metadata_ if isinstance(message.metadata_, dict) else {}
    error_payload = {
        "channel": channel,
        "error": error,
    }
    if status_code is not None:
        error_payload["status_code"] = status_code
    if response_text:
        error_payload["response_text"] = response_text[:500]
    metadata["send_error"] = error_payload
    message.metadata_ = metadata


def _resolve_integration_target(
    db: Session,
    channel_type: ChannelType,
    target_id: str | None,
) -> IntegrationTarget | None:
    if target_id:
        target = db.get(IntegrationTarget, coerce_uuid(target_id))
        if not target:
            raise HTTPException(status_code=404, detail="Integration target not found")
        return target

    # Map channel types to connector types
    connector_type_map = {
        ChannelType.whatsapp: ConnectorType.whatsapp,
        ChannelType.email: ConnectorType.email,
        ChannelType.facebook_messenger: ConnectorType.facebook,
        ChannelType.instagram_dm: ConnectorType.facebook,  # Instagram uses same connector as Facebook
    }
    connector_type = connector_type_map.get(channel_type, ConnectorType.email)

    return (
        db.query(IntegrationTarget)
        .filter(IntegrationTarget.target_type == IntegrationTargetType.crm)
        .filter(IntegrationTarget.is_active.is_(True))
        .join(ConnectorConfig, ConnectorConfig.id == IntegrationTarget.connector_config_id)
        .filter(ConnectorConfig.connector_type == connector_type)
        .order_by(IntegrationTarget.created_at.desc())
        .first()
    )


def _resolve_connector_config(
    db: Session,
    target: IntegrationTarget | None,
    channel_type: ChannelType,
) -> ConnectorConfig | None:
    if not target or not target.connector_config_id:
        return None
    config = db.get(ConnectorConfig, target.connector_config_id)
    if not config:
        return None

    # Map channel types to expected connector types
    expected_map = {
        ChannelType.whatsapp: ConnectorType.whatsapp,
        ChannelType.email: ConnectorType.email,
        ChannelType.facebook_messenger: ConnectorType.facebook,
        ChannelType.instagram_dm: ConnectorType.facebook,
    }
    expected = expected_map.get(channel_type, ConnectorType.email)

    if config.connector_type != expected:
        raise HTTPException(status_code=400, detail="Connector type mismatch")
    return config


def _smtp_config_from_connector(config: ConnectorConfig) -> dict | None:
    if not config.metadata_:
        return None
    smtp = config.metadata_.get("smtp") if isinstance(config.metadata_, dict) else None
    if not smtp:
        return None
    smtp_config = dict(smtp)
    auth_config = config.auth_config or {}
    if auth_config.get("username"):
        smtp_config["username"] = auth_config.get("username")
    if auth_config.get("password"):
        smtp_config["password"] = auth_config.get("password")
    if auth_config.get("from_email"):
        smtp_config["from_email"] = auth_config.get("from_email")
    if auth_config.get("from_name"):
        smtp_config["from_name"] = auth_config.get("from_name")
    return smtp_config


def _resolve_person_for_contact(contact: Person) -> str:
    return str(contact.id)


def _normalize_email_address(address: str | None) -> str | None:
    if not address:
        return None
    candidate = address.strip().lower()
    return candidate or None


def _normalize_phone_address(value: str | None) -> str | None:
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits or None


def _normalize_channel_address(channel_type: ChannelType, address: str | None) -> str | None:
    if not address:
        return None
    if channel_type == ChannelType.email:
        return _normalize_email_address(address)
    if channel_type == ChannelType.whatsapp:
        return _normalize_phone_address(address)
    return address.strip()


def _extract_self_email_addresses(config: ConnectorConfig | None) -> set[str]:
    addresses: set[str] = set()
    if not config:
        return addresses
    auth_config = config.auth_config if isinstance(config.auth_config, dict) else {}
    metadata = config.metadata_ if isinstance(config.metadata_, dict) else {}
    smtp_config = metadata.get("smtp") if isinstance(metadata.get("smtp"), dict) else {}

    for value in (
        auth_config.get("username"),
        auth_config.get("from_email"),
        auth_config.get("email"),
        metadata.get("from_email"),
        smtp_config.get("username"),
        smtp_config.get("from_email"),
        smtp_config.get("from"),
    ):
        normalized = _normalize_email_address(value) if isinstance(value, str) else None
        if normalized:
            addresses.add(normalized)
    return addresses


def _metadata_indicates_self(metadata: dict | None) -> bool:
    if not isinstance(metadata, dict):
        return False
    if metadata.get("is_echo") or metadata.get("from_me") or metadata.get("sent_by_business"):
        return True
    sender_type = metadata.get("sender_type") or metadata.get("author_type")
    if isinstance(sender_type, str) and sender_type.lower() in {
        "business",
        "agent",
        "system",
        "page",
        "company",
    }:
        return True
    direction = metadata.get("direction")
    if isinstance(direction, str) and direction.lower() in {"outbound", "sent", "business"}:
        return True
    return False


def _metadata_indicates_comment(metadata: dict | None) -> bool:
    if not isinstance(metadata, dict):
        return False
    if metadata.get("comment") or metadata.get("comment_id"):
        return True
    source = metadata.get("source")
    if isinstance(source, str) and source.lower() == "comment":
        return True
    msg_type = metadata.get("type")
    if isinstance(msg_type, str) and msg_type.lower() == "comment":
        return True
    return False


def _extract_whatsapp_business_number(
    metadata: dict | None,
    config: ConnectorConfig | None,
) -> str | None:
    if isinstance(metadata, dict):
        for key in (
            "display_phone_number",
            "business_number",
            "from_number",
            "phone_number",
        ):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value
    if not config:
        return None
    config_metadata = config.metadata_ if isinstance(config.metadata_, dict) else {}
    for key in ("display_phone_number", "business_number", "from_number", "phone_number"):
        value = config_metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value
    auth_config = config.auth_config if isinstance(config.auth_config, dict) else {}
    for key in ("display_phone_number", "business_number", "from_number", "phone_number"):
        value = auth_config.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _is_self_email_message(
    payload: EmailWebhookPayload,
    config: ConnectorConfig | None,
) -> bool:
    if _metadata_indicates_self(payload.metadata):
        return True
    sender = _normalize_email_address(payload.contact_address)
    if not sender:
        return False
    self_addresses = _extract_self_email_addresses(config)
    if not self_addresses:
        return False
    return sender in self_addresses


def _is_self_whatsapp_message(
    payload: WhatsAppWebhookPayload,
    config: ConnectorConfig | None,
) -> bool:
    if _metadata_indicates_self(payload.metadata):
        return True
    business_number = _extract_whatsapp_business_number(payload.metadata, config)
    if not business_number:
        return False
    sender = _normalize_phone_address(payload.contact_address)
    owner = _normalize_phone_address(business_number)
    if not sender or not owner:
        return False
    return sender == owner


def _extract_subscription_identifiers(metadata: dict | None) -> dict:
    identifiers = {
        "account_id": None,
        "subscriber_id": None,
        "account_number": None,
        "subscriber_number": None,
        "customer_id": None,
    }
    if not metadata or not isinstance(metadata, dict):
        return identifiers

    for key in identifiers:
        value = metadata.get(key)
        if value:
            identifiers[key] = str(value).strip()

    customer = metadata.get("customer")
    if isinstance(customer, dict):
        customer_id = customer.get("id") or customer.get("customer_id")
        account_number = customer.get("account_number")
        subscriber_number = customer.get("subscriber_number")
        if customer_id and not identifiers["customer_id"]:
            identifiers["customer_id"] = str(customer_id).strip()
        if account_number and not identifiers["account_number"]:
            identifiers["account_number"] = str(account_number).strip()
        if subscriber_number and not identifiers["subscriber_number"]:
            identifiers["subscriber_number"] = str(subscriber_number).strip()

    return identifiers


def _subscriber_query_options():
    return [
        selectinload(Subscriber.accounts)
        .selectinload(SubscriberAccount.account_roles)
        .selectinload(AccountRole.person)
        .selectinload(Person.channels),
        selectinload(Subscriber.person),
    ]


def _account_query_options():
    return [
        selectinload(SubscriberAccount.subscriber).selectinload(Subscriber.person),
        selectinload(SubscriberAccount.account_roles)
        .selectinload(AccountRole.person)
        .selectinload(Person.channels),
    ]


def _select_account_from_subscriber(subscriber: Subscriber | None) -> SubscriberAccount | None:
    if not subscriber or not subscriber.accounts:
        return None
    for account in subscriber.accounts:
        if account.status == AccountStatus.active:
            return account
    return subscriber.accounts[0]


def _load_account_with_context(db: Session, account_id) -> SubscriberAccount | None:
    return (
        db.query(SubscriberAccount)
        .options(*_account_query_options())
        .filter(SubscriberAccount.id == account_id)
        .first()
    )


def _load_subscriber_with_context(db: Session, subscriber_id) -> Subscriber | None:
    return (
        db.query(Subscriber)
        .options(*_subscriber_query_options())
        .filter(Subscriber.id == subscriber_id)
        .first()
    )


def _resolve_account_from_identifiers(
    db: Session,
    address: str | None,
    channel_type: ChannelType,
    metadata: dict | None,
) -> tuple[SubscriberAccount | None, Subscriber | None]:
    def _resolve_account_from_email(email_value: str | None) -> SubscriberAccount | None:
        normalized_email = _normalize_email_address(email_value)
        if not normalized_email:
            return None
        channel = (
            db.query(PersonChannel)
            .filter(PersonChannel.channel_type == PersonChannelType.email)
            .filter(func.lower(PersonChannel.address) == normalized_email)
            .first()
        )
        if channel:
            return (
                db.query(SubscriberAccount)
                .options(*_account_query_options())
                .join(AccountRole, AccountRole.account_id == SubscriberAccount.id)
                .filter(AccountRole.person_id == channel.person_id)
                .first()
            )
        person = db.query(Person).filter(func.lower(Person.email) == normalized_email).first()
        if person:
            return (
                db.query(SubscriberAccount)
                .options(*_account_query_options())
                .join(AccountRole, AccountRole.account_id == SubscriberAccount.id)
                .filter(AccountRole.person_id == person.id)
                .first()
            )
        return None

    def _resolve_account_from_phone(phone_value: str | None) -> SubscriberAccount | None:
        raw_value = phone_value.strip() if isinstance(phone_value, str) else None
        normalized_phone = _normalize_phone_address(phone_value)
        if not normalized_phone and not raw_value:
            return None
        channel = (
            db.query(PersonChannel)
            .filter(PersonChannel.channel_type == PersonChannelType.phone)
            .filter(
                or_(
                    PersonChannel.address == normalized_phone,
                    PersonChannel.address == raw_value,
                )
            )
            .first()
        )
        if channel:
            return (
                db.query(SubscriberAccount)
                .options(*_account_query_options())
                .join(AccountRole, AccountRole.account_id == SubscriberAccount.id)
                .filter(AccountRole.person_id == channel.person_id)
                .first()
            )
        person = db.query(Person).filter(
            or_(
                Person.phone == normalized_phone,
                Person.phone == raw_value,
            )
        ).first()
        if person:
            return (
                db.query(SubscriberAccount)
                .options(*_account_query_options())
                .join(AccountRole, AccountRole.account_id == SubscriberAccount.id)
                .filter(AccountRole.person_id == person.id)
                .first()
            )
        return None

    identifiers = _extract_subscription_identifiers(metadata)
    account = None
    subscriber = None

    account_id = identifiers.get("account_id")
    if account_id:
        try:
            account = _load_account_with_context(db, coerce_uuid(account_id))
        except Exception:
            account = None

    subscriber_id = identifiers.get("subscriber_id")
    if not account and subscriber_id:
        try:
            subscriber = _load_subscriber_with_context(db, coerce_uuid(subscriber_id))
        except Exception:
            subscriber = None
        account = _select_account_from_subscriber(subscriber)

    account_number = identifiers.get("account_number") or identifiers.get("customer_id")
    if not account and account_number:
        account = (
            db.query(SubscriberAccount)
            .options(*_account_query_options())
            .filter(SubscriberAccount.account_number == account_number)
            .first()
        )

    subscriber_number = identifiers.get("subscriber_number")
    if not account and subscriber_number:
        subscriber = (
            db.query(Subscriber)
            .options(*_subscriber_query_options())
            .filter(Subscriber.subscriber_number == subscriber_number)
            .first()
        )
        account = _select_account_from_subscriber(subscriber)

    if not account and address:
        if channel_type == ChannelType.email:
            email = _normalize_email_address(address)
            account = _resolve_account_from_email(email)
        elif channel_type == ChannelType.whatsapp:
            phone = _normalize_phone_address(address)
            account = _resolve_account_from_phone(phone)

    if not account and metadata and isinstance(metadata, dict):
        email = metadata.get("email")
        email_value = _normalize_email_address(email) if isinstance(email, str) else None
        if email_value:
            account = _resolve_account_from_email(email_value)
        if not account:
            phone = metadata.get("phone")
            phone_value = _normalize_phone_address(phone) if isinstance(phone, str) else None
            if phone_value:
                account = _resolve_account_from_phone(phone_value)

    if account:
        subscriber = account.subscriber

    return account, subscriber


def _resolve_display_name_from_account(account: SubscriberAccount | None) -> str | None:
    if not account:
        return None
    subscriber = account.subscriber
    if subscriber and subscriber.person:
        person = subscriber.person
        if person.organization:
            return person.organization.name
        return person.display_name or " ".join(
            part for part in [person.first_name, person.last_name] if part
        ).strip()
    if account.billing_person:
        return account.billing_person
    for role in account.account_roles or []:
        person = role.person
        if person:
            full_name = person.display_name or " ".join(
                part for part in [person.first_name, person.last_name] if part
            ).strip()
            if full_name:
                return full_name
    return None


def _resolve_email_from_account(account: SubscriberAccount | None) -> str | None:
    if not account:
        return None
    subscriber = account.subscriber
    if subscriber and subscriber.person and subscriber.person.email:
        return subscriber.person.email
    for role in account.account_roles or []:
        person = role.person
        if not person:
            continue
        if person.email:
            return person.email
        for channel in person.channels or []:
            if channel.channel_type == PersonChannelType.email:
                if channel.is_primary:
                    return channel.address
        for channel in person.channels or []:
            if channel.channel_type == PersonChannelType.email:
                return channel.address
    return None


def _resolve_phone_from_account(account: SubscriberAccount | None) -> str | None:
    if not account:
        return None
    subscriber = account.subscriber
    if subscriber and subscriber.person and subscriber.person.phone:
        return subscriber.person.phone
    for role in account.account_roles or []:
        person = role.person
        if not person:
            continue
        if person.phone:
            return person.phone
        for channel in person.channels or []:
            if channel.channel_type == PersonChannelType.phone:
                if channel.is_primary:
                    return channel.address
        for channel in person.channels or []:
            if channel.channel_type == PersonChannelType.phone:
                return channel.address
    return None


def _ensure_person_channel(
    db: Session,
    person: Person,
    channel_type: ChannelType,
    address: str,
):
    person_channel_type = PersonChannelType(channel_type.value)
    normalized_address = _normalize_channel_address(channel_type, address) or address
    channel = (
        db.query(PersonChannel)
        .filter(PersonChannel.person_id == person.id)
        .filter(PersonChannel.channel_type == person_channel_type)
        .filter(
            or_(
                PersonChannel.address == normalized_address,
                PersonChannel.address == address.strip(),
            )
        )
        .first()
    )
    if channel:
        return channel
    has_primary = (
        db.query(PersonChannel)
        .filter(PersonChannel.person_id == person.id)
        .filter(PersonChannel.channel_type == person_channel_type)
        .filter(PersonChannel.is_primary.is_(True))
        .first()
    )
    channel = PersonChannel(
        person_id=person.id,
        channel_type=person_channel_type,
        address=normalized_address,
        is_primary=has_primary is None,
    )
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return channel


def _resolve_person_for_inbound(
    db: Session,
    channel_type: ChannelType,
    address: str,
    display_name: str | None,
    account: SubscriberAccount | None,
):
    person_channel_type = PersonChannelType(channel_type.value)
    normalized_address = _normalize_channel_address(channel_type, address)
    existing_channel = (
        db.query(PersonChannel)
        .filter(PersonChannel.channel_type == person_channel_type)
        .filter(
            or_(
                PersonChannel.address == normalized_address,
                PersonChannel.address == address.strip(),
            )
        )
        .first()
    )
    if existing_channel:
        return existing_channel.person, existing_channel

    person = None
    if account and account.subscriber and account.subscriber.person_id:
        person = db.get(Person, account.subscriber.person_id)

    if not person and channel_type == ChannelType.email:
        person = (
            db.query(Person)
            .filter(func.lower(Person.email) == normalized_address)
            .first()
        )
    if not person and channel_type == ChannelType.whatsapp:
        person = (
            db.query(Person)
            .filter(
                or_(
                    Person.phone == normalized_address,
                    Person.phone == address.strip(),
                )
            )
            .first()
        )

    if person:
        channel = _ensure_person_channel(
            db,
            person,
            channel_type,
            normalized_address or address,
        )
        if channel_type == ChannelType.email and normalized_address:
            if not person.email or person.email.endswith("@example.invalid"):
                person.email = normalized_address
                db.commit()
                db.refresh(person)
        if display_name and not person.display_name:
            person.display_name = display_name
            db.commit()
            db.refresh(person)
        return person, channel

    return contact_service.get_or_create_contact_by_channel(
        db,
        channel_type,
        normalized_address or address,
        display_name,
    )


def _apply_account_to_person(
    db: Session,
    person: Person,
    account: SubscriberAccount | None,
):
    if not account:
        return
    subscriber = account.subscriber
    metadata = dict(person.metadata_ or {}) if isinstance(person.metadata_, dict) else {}
    updated = False

    display_name = _resolve_display_name_from_account(account)
    if display_name and not person.display_name:
        person.display_name = display_name
        updated = True

    email = _resolve_email_from_account(account)
    normalized_email = _normalize_email_address(email)
    if normalized_email and not person.email:
        person.email = normalized_email
        updated = True

    phone = _resolve_phone_from_account(account)
    normalized_phone = _normalize_phone_address(phone)
    if normalized_phone and not person.phone:
        person.phone = normalized_phone
        updated = True

    if account.id:
        if metadata.get("account_id") != str(account.id):
            metadata["account_id"] = str(account.id)
            updated = True
        if account.account_number and metadata.get("account_number") != account.account_number:
            metadata["account_number"] = account.account_number
            updated = True
        if account.account_number and metadata.get("customer_id") != account.account_number:
            metadata["customer_id"] = account.account_number
            updated = True
        if account.status and metadata.get("account_status") != account.status.value:
            metadata["account_status"] = account.status.value
            updated = True
    if subscriber:
        if metadata.get("subscriber_id") != str(subscriber.id):
            metadata["subscriber_id"] = str(subscriber.id)
            updated = True
        if subscriber.subscriber_number and metadata.get("subscriber_number") != subscriber.subscriber_number:
            metadata["subscriber_number"] = subscriber.subscriber_number
            updated = True

    if updated:
        person.metadata_ = metadata or None
        db.commit()
        db.refresh(person)


def _find_duplicate_inbound_message(
    db: Session,
    channel_type: ChannelType,
    person_channel_id,
    channel_target_id,
    message_id: str | None,
    subject: str | None,
    body: str,
    received_at: datetime,
    dedupe_across_targets: bool = False,
) -> Message | None:
    if message_id:
        existing_query = (
            db.query(Message)
            .filter(Message.channel_type == channel_type)
            .filter(Message.external_id == message_id)
        )
        if not dedupe_across_targets:
            if channel_target_id:
                existing_query = existing_query.filter(Message.channel_target_id == channel_target_id)
            else:
                existing_query = existing_query.filter(Message.channel_target_id.is_(None))
        return existing_query.first()

    # Fallback dedupe when providers omit message_id; keep a tight time window to avoid collisions.
    time_window_start = received_at - timedelta(minutes=5)
    time_window_end = received_at + timedelta(minutes=5)
    existing_query = (
        db.query(Message)
        .filter(Message.channel_type == channel_type)
        .filter(Message.direction == MessageDirection.inbound)
        .filter(Message.person_channel_id == person_channel_id)
        .filter(Message.body == body)
        .filter(Message.received_at >= time_window_start)
        .filter(Message.received_at <= time_window_end)
    )
    if subject:
        existing_query = existing_query.filter(Message.subject == subject)
    if channel_target_id:
        existing_query = existing_query.filter(Message.channel_target_id == channel_target_id)
    else:
        existing_query = existing_query.filter(Message.channel_target_id.is_(None))
    return existing_query.first()


def _resolve_meta_account_id(
    db: Session,
    conversation_id,
    channel_type: ChannelType,
) -> str | None:
    if channel_type == ChannelType.facebook_messenger:
        metadata_key = "page_id"
    elif channel_type == ChannelType.instagram_dm:
        metadata_key = "instagram_account_id"
    else:
        return None

    message = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .filter(Message.channel_type == channel_type)
        .filter(Message.direction == MessageDirection.inbound)
        .filter(Message.metadata_.isnot(None))
        .order_by(func.coalesce(Message.received_at, Message.created_at).desc())
        .first()
    )
    if not message or not isinstance(message.metadata_, dict):
        return None
    return message.metadata_.get(metadata_key)


def _get_last_inbound_message(db: Session, conversation_id) -> Message | None:
    return (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .filter(Message.direction == MessageDirection.inbound)
        .order_by(func.coalesce(Message.received_at, Message.created_at).desc())
        .first()
    )


def _build_conversation_summary(db: Session, conversation, message: Message) -> dict:
    unread_count = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .filter(Message.direction == MessageDirection.inbound)
        .filter(Message.status == MessageStatus.received)
        .filter(Message.read_at.is_(None))
        .count()
    )
    last_message_at = message.received_at or message.sent_at or message.created_at
    preview = message.body or ""
    if len(preview) > 100:
        preview = preview[:100] + "..."
    return {
        "preview": preview,
        "last_message_at": last_message_at.isoformat() if last_message_at else None,
        "channel": message.channel_type.value if message.channel_type else None,
        "unread_count": unread_count,
    }


def receive_whatsapp_message(db: Session, payload: WhatsAppWebhookPayload):
    received_at = payload.received_at or _now()
    target = None
    if payload.channel_target_id:
        target = _resolve_integration_target(
            db,
            ChannelType.whatsapp,
            str(payload.channel_target_id),
        )
    else:
        target = _resolve_integration_target(db, ChannelType.whatsapp, None)

    config = _resolve_connector_config(db, target, ChannelType.whatsapp) if target else None
    if _is_self_whatsapp_message(payload, config):
        logger.info(
            "whatsapp_inbound_skip_self contact_address=%s",
            payload.contact_address,
        )
        return None

    account, subscriber = _resolve_account_from_identifiers(
        db,
        payload.contact_address,
        ChannelType.whatsapp,
        payload.metadata,
    )
    person, channel = _resolve_person_for_inbound(
        db,
        ChannelType.whatsapp,
        payload.contact_address,
        payload.contact_name,
        account,
    )
    _apply_account_to_person(db, person, account)
    existing = _find_duplicate_inbound_message(
        db,
        ChannelType.whatsapp,
        channel.id,
        target.id if target else None,
        payload.message_id,
        None,
        payload.body,
        received_at,
    )
    if existing:
        return existing
    person_id = _resolve_person_for_contact(person)
    subscriber_id = None
    account_id = None
    if account:
        account_id = account.id
        subscriber_id = account.subscriber_id
    elif subscriber:
        subscriber_id = subscriber.id
    conversation = conversation_service.resolve_open_conversation_for_channel(
        db,
        person_id,
        ChannelType.whatsapp,
    )
    if not conversation:
        conversation = conversation_service.Conversations.create(
            db,
            ConversationCreate(
                person_id=person_id,
                is_active=True,  # Ensure new conversations are visible in the inbox UI.
            ),
        )
    elif not conversation.is_active:
        conversation.is_active = True
        conversation.status = ConversationStatus.open
        db.commit()
        db.refresh(conversation)
    message = conversation_service.Messages.create(
        db,
        MessageCreate(
            conversation_id=conversation.id,
            person_channel_id=channel.id,
            channel_target_id=target.id if target else None,
            channel_type=ChannelType.whatsapp,
            direction=MessageDirection.inbound,
            status=MessageStatus.received,
            body=payload.body,
            external_id=payload.message_id,
            received_at=received_at,
            metadata=payload.metadata,
        ),
    )
    from app.websocket.broadcaster import broadcast_new_message
    broadcast_new_message(message, conversation)
    from app.websocket.broadcaster import broadcast_conversation_summary
    broadcast_conversation_summary(
        conversation.id,
        _build_conversation_summary(db, conversation, message),
    )
    return message


def receive_email_message(db: Session, payload: EmailWebhookPayload):
    logger.debug(
        "receive_email_message_start subject=%s from=%s metadata_keys=%s",
        payload.subject,
        payload.contact_address,
        list(payload.metadata.keys()) if isinstance(payload.metadata, dict) else [],
    )
    received_at = payload.received_at or _now()
    metadata = payload.metadata if isinstance(payload.metadata, dict) else {}
    target = None
    if payload.channel_target_id:
        target = _resolve_integration_target(
            db,
            ChannelType.email,
            str(payload.channel_target_id),
        )
    else:
        target = _resolve_integration_target(db, ChannelType.email, None)

    config = _resolve_connector_config(db, target, ChannelType.email) if target else None
    if _is_self_email_message(payload, config):
        logger.info("email_inbound_skip_self from=%s", payload.contact_address)
        return None

    account, subscriber = _resolve_account_from_identifiers(
        db,
        payload.contact_address,
        ChannelType.email,
        metadata,
    )
    person, channel = _resolve_person_for_inbound(
        db,
        ChannelType.email,
        payload.contact_address,
        payload.contact_name,
        account,
    )
    _apply_account_to_person(db, person, account)
    external_id = _normalize_external_id(payload.message_id)
    if not external_id:
        external_id = _build_inbound_dedupe_id(
            ChannelType.email,
            payload.contact_address,
            payload.subject,
            payload.body,
            payload.received_at,
        )
    existing = _find_duplicate_inbound_message(
        db,
        ChannelType.email,
        channel.id,
        target.id if target else None,
        external_id,
        payload.subject,
        payload.body,
        received_at,
        dedupe_across_targets=True,
    )
    logger.info(
        "EMAIL_MESSAGE_RECEIVED subject=%s message_id=%s duplicate=%s",
        payload.subject,
        payload.message_id,
        existing is not None,
    )
    if existing:
        return existing
    person_id = _resolve_person_for_contact(person)
    subscriber_id = None
    account_id = None
    if account:
        account_id = account.id
        subscriber_id = account.subscriber_id
    elif subscriber:
        subscriber_id = subscriber.id
    in_reply_to = _get_metadata_value(metadata, "in_reply_to")
    references = _get_metadata_value(metadata, "references")
    crm_header = _get_metadata_value(metadata, "x-crm-id") or _get_metadata_value(
        metadata, "x-crm-conversation-id"
    )
    has_thread_headers = bool(
        payload.message_id
        or in_reply_to
        or references
        or crm_header
        or _extract_conversation_tokens(payload.subject)
    )

    conversation = _resolve_conversation_from_email_metadata(
        db,
        payload.subject,
        metadata,
    )
    if not conversation and _metadata_indicates_comment(metadata) and payload.subject:
        conversation = (
            db.query(Conversation)
            .filter(Conversation.person_id == coerce_uuid(person_id))
            .filter(Conversation.subject.ilike(payload.subject))
            .order_by(Conversation.updated_at.desc())
            .first()
        )
    if not conversation:
        conversation = conversation_service.resolve_open_conversation_for_channel(
            db,
            person_id,
            ChannelType.email,
        )
        if conversation and not has_thread_headers:
            conv_meta = conversation.metadata_ if isinstance(conversation.metadata_, dict) else {}
            warnings = conv_meta.get("warnings")
            if not isinstance(warnings, list):
                warnings = []
            warnings.append(
                {
                    "type": "email_reply_without_headers",
                    "message_id": payload.message_id,
                    "received_at": received_at.isoformat() if received_at else None,
                }
            )
            conv_meta["warnings"] = warnings
            conversation.metadata_ = conv_meta
            db.commit()
    if not conversation:
        conversation = conversation_service.Conversations.create(
            db,
            ConversationCreate(
                person_id=person_id,
                subject=payload.subject,
                is_active=True,  # Ensure new conversations are visible in the inbox UI.
            ),
        )
    elif not conversation.is_active:
        conversation.is_active = True
        conversation.status = ConversationStatus.open
        db.commit()
        db.refresh(conversation)
    elif conversation.person_id != person_id:
        logger.warning(
            "email_reply_conversation_mismatch conversation_id=%s sender_person_id=%s conversation_person_id=%s",
            conversation.id,
            person_id,
            conversation.person_id,
        )
    message = conversation_service.Messages.create(
        db,
        MessageCreate(
            conversation_id=conversation.id,
            person_channel_id=channel.id,
            channel_target_id=target.id if target else None,
            channel_type=ChannelType.email,
            direction=MessageDirection.inbound,
            status=MessageStatus.received,
            subject=payload.subject,
            body=payload.body,
            external_id=external_id,
            received_at=received_at,
            metadata=metadata,
        ),
    )
    from app.websocket.broadcaster import broadcast_new_message
    broadcast_new_message(message, conversation)
    from app.websocket.broadcaster import broadcast_conversation_summary
    broadcast_conversation_summary(
        conversation.id,
        _build_conversation_summary(db, conversation, message),
    )
    return message


def send_message(db: Session, payload: InboxSendRequest, author_id: str | None = None):
    conversation = conversation_service.Conversations.get(db, str(payload.conversation_id))
    person = conversation.person
    if not person:
        raise HTTPException(status_code=404, detail="Contact not found")

    last_inbound = _get_last_inbound_message(db, conversation.id)
    if last_inbound and last_inbound.channel_type != payload.channel_type:
        raise HTTPException(
            status_code=400,
            detail="Reply channel does not match the originating channel",
        )

    resolved_channel_target_id = payload.channel_target_id
    if last_inbound and last_inbound.channel_target_id:
        if resolved_channel_target_id and last_inbound.channel_target_id != resolved_channel_target_id:
            raise HTTPException(
                status_code=400,
                detail="Reply channel target does not match the originating channel",
            )
        if not resolved_channel_target_id:
            resolved_channel_target_id = last_inbound.channel_target_id

    if payload.channel_type == ChannelType.email and not payload.person_channel_id:
        email_address = _normalize_email_address(person.email) if person.email else None
        if email_address:
            existing_channel = (
                db.query(PersonChannel)
                .filter(PersonChannel.person_id == person.id)
                .filter(PersonChannel.channel_type == PersonChannelType.email)
                .filter(func.lower(PersonChannel.address) == email_address)
                .first()
            )
            if not existing_channel:
                has_primary = (
                    db.query(PersonChannel)
                    .filter(PersonChannel.person_id == person.id)
                    .filter(PersonChannel.channel_type == PersonChannelType.email)
                    .filter(PersonChannel.is_primary.is_(True))
                    .first()
                )
                db.add(
                    PersonChannel(
                        person_id=person.id,
                        channel_type=PersonChannelType.email,
                        address=email_address,
                        is_primary=has_primary is None,
                    )
                )
                db.flush()

    person_channel = None
    if payload.person_channel_id:
        person_channel = db.get(PersonChannel, payload.person_channel_id)
        if not person_channel:
            raise HTTPException(status_code=400, detail="Contact channel not found")
        if person_channel.person_id != person.id:
            raise HTTPException(status_code=400, detail="Contact channel mismatch")
        if person_channel.channel_type.value != payload.channel_type.value:
            raise HTTPException(status_code=400, detail="Contact channel type mismatch")
    else:
        person_channel = conversation_service.resolve_person_channel(
            db, str(person.id), payload.channel_type
        )

    if not person_channel:
        raise HTTPException(status_code=400, detail="Contact channel not found")

    target = _resolve_integration_target(
        db,
        payload.channel_type,
        str(resolved_channel_target_id) if resolved_channel_target_id else None,
    )
    config = _resolve_connector_config(db, target, payload.channel_type) if target else None

    rendered_body = _render_personalization(payload.body, payload.personalization)

    if payload.channel_type == ChannelType.email:
        if not person_channel.address:
            raise HTTPException(status_code=400, detail="Recipient email missing")
        message = conversation_service.Messages.create(
            db,
            MessageCreate(
                conversation_id=conversation.id,
                person_channel_id=person_channel.id,
                channel_target_id=target.id if target else None,
                channel_type=payload.channel_type,
                direction=MessageDirection.outbound,
                status=MessageStatus.queued,
                subject=payload.subject,
                body=rendered_body,
                author_id=coerce_uuid(author_id) if author_id else None,
                sent_at=_now(),
            ),
        )
        sent = False
        if config:
            smtp_config = _smtp_config_from_connector(config)
            if smtp_config:
                sent = email_service.send_email_with_config(
                    smtp_config,
                    person_channel.address,
                    payload.subject or "Message",
                    rendered_body,
                    rendered_body,
                )
        if not sent:
            sent = email_service.send_email(
                db,
                person_channel.address,
                payload.subject or "Message",
                rendered_body,
                rendered_body,
            )
        message.status = MessageStatus.sent if sent else MessageStatus.failed
        db.commit()
        db.refresh(message)
        from app.websocket.broadcaster import broadcast_message_status
        broadcast_message_status(str(message.id), str(message.conversation_id), message.status.value)
        return message

    if payload.channel_type == ChannelType.whatsapp:
        if not config:
            raise HTTPException(status_code=400, detail="WhatsApp connector not configured")
        token = None
        if config.auth_config:
            token = config.auth_config.get("token") or config.auth_config.get("access_token")
        if not token:
            raise HTTPException(status_code=400, detail="WhatsApp access token missing")
        phone_number_id = None
        if config.metadata_:
            phone_number_id = config.metadata_.get("phone_number_id")
        if config.auth_config and not phone_number_id:
            phone_number_id = config.auth_config.get("phone_number_id")
        if not phone_number_id:
            raise HTTPException(status_code=400, detail="WhatsApp phone_number_id missing")
        message = conversation_service.Messages.create(
            db,
            MessageCreate(
                conversation_id=conversation.id,
                person_channel_id=person_channel.id,
                channel_target_id=target.id if target else None,
                channel_type=payload.channel_type,
                direction=MessageDirection.outbound,
                status=MessageStatus.queued,
                subject=payload.subject,
                body=rendered_body,
                author_id=coerce_uuid(author_id) if author_id else None,
                sent_at=_now(),
            ),
        )
        base_url = config.base_url or "https://graph.facebook.com/v19.0"
        payload_data = {
            "messaging_product": "whatsapp",
            "to": person_channel.address,
            "type": "text",
            "text": {"body": rendered_body},
        }
        headers = {"Authorization": f"Bearer {token}"}
        if config.headers:
            headers.update(config.headers)
        try:
            whatsapp_timeout = config.timeout_sec or _get_whatsapp_api_timeout(db)
            response = httpx.post(
                f"{base_url.rstrip('/')}/{phone_number_id}/messages",
                json=payload_data,
                headers=headers,
                timeout=whatsapp_timeout,
            )
            response.raise_for_status()
            data = response.json() if response.content else {}
            message.status = MessageStatus.sent
            message.external_id = data.get("messages", [{}])[0].get("id")
        except httpx.HTTPError as exc:
            message.status = MessageStatus.failed
            status_code = None
            response_text = None
            if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
                status_code = exc.response.status_code
                response_text = exc.response.text
            _set_message_send_error(
                message,
                "whatsapp",
                str(exc),
                status_code=status_code,
                response_text=response_text,
            )
            if status_code in (401, 403):
                logger.error(
                    "whatsapp_send_auth_failed conversation_id=%s status=%s body=%s",
                    conversation.id,
                    status_code,
                    response_text,
                )
            else:
                logger.error(
                    "whatsapp_send_failed conversation_id=%s status=%s body=%s error=%s",
                    conversation.id,
                    status_code,
                    response_text,
                    exc,
                )
        db.commit()
        db.refresh(message)
        from app.websocket.broadcaster import broadcast_message_status
        broadcast_message_status(str(message.id), str(message.conversation_id), message.status.value)
        return message

    # Facebook Messenger sending
    if payload.channel_type == ChannelType.facebook_messenger:
        from app.services import meta_messaging
        account_id = _resolve_meta_account_id(db, conversation.id, payload.channel_type)
        if not last_inbound or not last_inbound.received_at:
            raise HTTPException(status_code=400, detail="Meta reply window expired")
        if (datetime.now(timezone.utc) - last_inbound.received_at).total_seconds() > 24 * 3600:
            raise HTTPException(status_code=400, detail="Meta reply window expired")

        message = conversation_service.Messages.create(
            db,
            MessageCreate(
                conversation_id=conversation.id,
                person_channel_id=person_channel.id,
                channel_target_id=target.id if target else None,
                channel_type=payload.channel_type,
                direction=MessageDirection.outbound,
                status=MessageStatus.queued,
                body=rendered_body,
                author_id=coerce_uuid(author_id) if author_id else None,
                sent_at=_now(),
            ),
        )

        try:
            result = meta_messaging.send_facebook_message_sync(
                db,
                person_channel.address,
                rendered_body,
                target,
                account_id=account_id,
            )
            message.status = MessageStatus.sent
            message.external_id = result.get("message_id")
        except Exception as exc:
            logger.error("facebook_messenger_send_failed conversation_id=%s error=%s", conversation.id, exc)
            message.status = MessageStatus.failed
            _set_message_send_error(message, "facebook_messenger", str(exc))

        db.commit()
        db.refresh(message)
        from app.websocket.broadcaster import broadcast_message_status
        broadcast_message_status(str(message.id), str(message.conversation_id), message.status.value)
        return message

    # Instagram DM sending
    if payload.channel_type == ChannelType.instagram_dm:
        from app.services import meta_messaging
        account_id = _resolve_meta_account_id(db, conversation.id, payload.channel_type)
        if not last_inbound or not last_inbound.received_at:
            raise HTTPException(status_code=400, detail="Meta reply window expired")
        if (datetime.now(timezone.utc) - last_inbound.received_at).total_seconds() > 24 * 3600:
            raise HTTPException(status_code=400, detail="Meta reply window expired")

        message = conversation_service.Messages.create(
            db,
            MessageCreate(
                conversation_id=conversation.id,
                person_channel_id=person_channel.id,
                channel_target_id=target.id if target else None,
                channel_type=payload.channel_type,
                direction=MessageDirection.outbound,
                status=MessageStatus.queued,
                body=rendered_body,
                author_id=coerce_uuid(author_id) if author_id else None,
                sent_at=_now(),
            ),
        )

        try:
            result = meta_messaging.send_instagram_message_sync(
                db,
                person_channel.address,
                rendered_body,
                target,
                account_id=account_id,
            )
            message.status = MessageStatus.sent
            message.external_id = result.get("message_id")
        except Exception as exc:
            logger.error("instagram_dm_send_failed conversation_id=%s error=%s", conversation.id, exc)
            message.status = MessageStatus.failed
            _set_message_send_error(message, "instagram_dm", str(exc))

        db.commit()
        db.refresh(message)
        from app.websocket.broadcaster import broadcast_message_status
        broadcast_message_status(str(message.id), str(message.conversation_id), message.status.value)
        return message

    raise HTTPException(status_code=400, detail="Unsupported channel type")


def create_email_connector_target(
    db: Session,
    name: str,
    smtp: dict | None = None,
    imap: dict | None = None,
    pop3: dict | None = None,
    auth_config: dict | None = None,
):
    config = ConnectorConfig(
        name=name,
        connector_type=ConnectorType.email,
        auth_config=auth_config,
        metadata_={"smtp": smtp, "imap": imap, "pop3": pop3},
    )
    db.add(config)
    db.commit()
    db.refresh(config)

    target = IntegrationTarget(
        name=name,
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return target


def create_whatsapp_connector_target(
    db: Session,
    name: str,
    phone_number_id: str | None = None,
    auth_config: dict | None = None,
    base_url: str | None = None,
):
    metadata = {}
    if phone_number_id:
        metadata["phone_number_id"] = phone_number_id
    config = ConnectorConfig(
        name=name,
        connector_type=ConnectorType.whatsapp,
        auth_config=auth_config,
        base_url=base_url,
        metadata_=metadata or None,
    )
    db.add(config)
    db.commit()
    db.refresh(config)

    target = IntegrationTarget(
        name=name,
        target_type=IntegrationTargetType.crm,
        connector_config_id=config.id,
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return target


def ensure_email_polling_job(
    db: Session,
    target_id: str,
    interval_seconds: int | None = None,
    interval_minutes: int | None = None,
    name: str | None = None,
):
    if interval_minutes is not None:
        if interval_minutes < 1:
            raise HTTPException(status_code=400, detail="interval_minutes must be >= 1")
    elif interval_seconds is not None:
        if interval_seconds < 1:
            raise HTTPException(status_code=400, detail="interval_seconds must be >= 1")
    else:
        raise HTTPException(status_code=400, detail="interval_seconds must be >= 1")
    target = db.get(IntegrationTarget, coerce_uuid(target_id))
    if not target:
        raise HTTPException(status_code=404, detail="Integration target not found")
    if target.target_type != IntegrationTargetType.crm:
        raise HTTPException(status_code=400, detail="Target must be crm type")
    if not target.connector_config_id:
        raise HTTPException(status_code=400, detail="Target missing connector config")
    config = db.get(ConnectorConfig, target.connector_config_id)
    if not config or config.connector_type != ConnectorType.email:
        raise HTTPException(status_code=400, detail="Target is not email connector")

    interval_seconds_value = interval_seconds if interval_minutes is None else None
    interval_minutes_value = interval_minutes

    job = (
        db.query(IntegrationJob)
        .filter(IntegrationJob.target_id == target.id)
        .filter(IntegrationJob.job_type == IntegrationJobType.import_)
        .order_by(IntegrationJob.created_at.desc())
        .first()
    )
    if job:
        changed = (
            job.interval_minutes != interval_minutes_value
            or job.interval_seconds != interval_seconds_value
            or job.schedule_type != IntegrationScheduleType.interval
            or job.is_active is not True
        )
        if changed:
            logger.info("EMAIL_POLL_JOB_UPDATED job_id=%s target_id=%s", job.id, target.id)
        else:
            logger.info("EMAIL_POLL_JOB_SKIPPED job_id=%s target_id=%s", job.id, target.id)
        if interval_minutes_value is not None:
            job.interval_minutes = interval_minutes_value
            job.interval_seconds = None
        else:
            job.interval_seconds = interval_seconds_value
            job.interval_minutes = None
        job.schedule_type = IntegrationScheduleType.interval
        job.is_active = True
        db.commit()
        db.refresh(job)
        logger.info(
            "EMAIL_POLL_JOB_CALLED connector_id=%s interval_seconds=%s interval_minutes=%s job_id=%s",
            config.id,
            interval_seconds_value,
            interval_minutes_value,
            job.id,
        )
        return job
    job = IntegrationJob(
        target_id=target.id,
        name=name or f"{target.name} Email Polling",
        job_type=IntegrationJobType.import_,
        schedule_type=IntegrationScheduleType.interval,
        interval_seconds=interval_seconds_value,
        interval_minutes=interval_minutes_value,
        is_active=True,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    logger.info("EMAIL_POLL_JOB_CREATED job_id=%s target_id=%s", job.id, target.id)
    logger.info(
        "EMAIL_POLL_JOB_CALLED connector_id=%s interval_seconds=%s interval_minutes=%s job_id=%s",
        config.id,
        interval_seconds_value,
        interval_minutes_value,
        job.id,
    )
    return job


def poll_email_targets(db: Session, target_id: str | None = None) -> dict:
    query = db.query(IntegrationTarget).filter(
        IntegrationTarget.target_type == IntegrationTargetType.crm,
        IntegrationTarget.is_active.is_(True),
    )
    if target_id:
        query = query.filter(IntegrationTarget.id == coerce_uuid(target_id))
    targets = query.all()
    if not targets:
        logger.info("EMAIL_POLL_EXIT reason=no_targets")
        return {"processed": 0}

    email_connectors: list[ConnectorConfig] = []
    for target in targets:
        if not target.connector_config_id:
            continue
        config = db.get(ConnectorConfig, target.connector_config_id)
        if not config or config.connector_type != ConnectorType.email:
            continue
        email_connectors.append(config)

    logger.info(
        "EMAIL_POLL_START ts=%s targets=%s connectors=%s",
        datetime.now(timezone.utc).isoformat(),
        len(targets),
        len(email_connectors),
    )
    if not email_connectors:
        logger.info("EMAIL_POLL_EXIT reason=no_connectors")
        return {"processed": 0}

    processed_total = 0
    for config in email_connectors:
        result = email_polling.poll_email_connector(db, config)
        processed_total += int(result.get("processed") or 0)
    return {"processed": processed_total}
