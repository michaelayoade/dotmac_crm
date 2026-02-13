"""Outbound message sending for CRM inbox.

Handles sending messages across email, WhatsApp, Facebook Messenger,
Instagram DM, and chat widget channels.
"""

from __future__ import annotations

import base64
import os
import secrets
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from typing import cast as typing_cast

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.logging import get_logger
from app.logic.crm_inbox_logic import (
    ChannelType as LogicChannelType,
)
from app.logic.crm_inbox_logic import (
    LogicService,
    MessageContext,
)
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, MessageDirection, MessageStatus
from app.models.person import ChannelType as PersonChannelType
from app.models.person import Person, PersonChannel
from app.schemas.crm.conversation import MessageCreate
from app.schemas.crm.inbox import InboxSendRequest
from app.services import email as email_service
from app.services.common import coerce_uuid
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox import cache as inbox_cache
from app.services.crm.inbox._core import _store_external_message_id
from app.services.crm.inbox.circuit_breaker import CircuitBreaker, CircuitOpenError
from app.services.crm.inbox.errors import (
    InboxConfigError,
    InboxError,
    InboxNotFoundError,
    InboxValidationError,
)
from app.services.crm.inbox.observability import MESSAGE_PROCESSING_TIME, OUTBOUND_MESSAGES
from app.services.crm.inbox.rate_limit import RateLimitExceeded, build_rate_limit_key, check_rate_limit
from app.services.crm.inbox.templates import message_templates
from app.services.crm.inbox_connectors import (
    _get_whatsapp_api_timeout,
    _resolve_connector_config,
    _resolve_integration_target,
    _smtp_config_from_connector,
)

if TYPE_CHECKING:
    from app.models.connector import ConnectorConfig
    from app.models.crm.conversation import Conversation
    from app.models.integration import IntegrationTarget

logger = get_logger(__name__)
USE_INBOX_LOGIC_SERVICE = os.getenv("USE_INBOX_LOGIC_SERVICE", "0") == "1"
_logic_service = LogicService()
WHATSAPP_CIRCUIT = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
META_CIRCUIT = CircuitBreaker(failure_threshold=5, recovery_timeout=60)


class OutboundSendError(InboxError):
    """Base error for outbound send failures."""


class TransientOutboundError(OutboundSendError):
    """Retryable outbound send failure."""

    def __init__(self, detail: str, status_code: int = 503):
        super().__init__(
            code="outbound_transient_error",
            detail=detail,
            status_code=status_code,
            retryable=True,
        )


class PermanentOutboundError(OutboundSendError):
    """Non-retryable outbound send failure."""

    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(
            code="outbound_permanent_error",
            detail=detail,
            status_code=status_code,
            retryable=False,
        )


def _is_transient_status(status_code: int | None) -> bool:
    if status_code is None:
        return True
    if status_code in {408, 409, 425, 429}:
        return True
    return 500 <= status_code <= 599


def _is_transient_exception(exc: Exception, status_code: int | None = None) -> bool:
    if status_code is not None:
        return _is_transient_status(status_code)
    if isinstance(exc, InboxError):
        return exc.retryable
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        return _is_transient_status(exc.response.status_code)
    return bool(isinstance(exc, httpx.TransportError))


def _sleep_with_backoff(attempt: int, base: float, max_backoff: float) -> None:
    backoff = min(base * (2 ** max(attempt - 1, 0)), max_backoff)
    jitter = backoff * (secrets.randbelow(2500) / 10000)
    time.sleep(backoff + jitter)


def _now():
    return datetime.now(UTC)


def _broadcast_outbound_summary(db: Session, conversation: Conversation, message: Message) -> None:
    """Push updated preview/last-message time to subscribed inbox sidebars."""
    try:
        from app.websocket.broadcaster import broadcast_conversation_summary

        unread_count = (
            db.query(func.count(Message.id))
            .filter(Message.conversation_id == conversation.id)
            .filter(Message.direction == MessageDirection.inbound)
            .filter(Message.status == MessageStatus.received)
            .filter(Message.read_at.is_(None))
            .scalar()
            or 0
        )
        preview = (message.body or "").strip()
        if len(preview) > 100:
            preview = f"{preview[:100]}..."
        broadcast_conversation_summary(
            str(conversation.id),
            {
                "preview": preview or "New message sent",
                "last_message_at": (
                    (message.sent_at or message.created_at).isoformat()
                    if (message.sent_at or message.created_at)
                    else None
                ),
                "channel": message.channel_type.value if message.channel_type else None,
                "unread_count": int(unread_count),
            },
        )
    except Exception:
        logger.debug(
            "broadcast_outbound_summary_failed conversation_id=%s message_id=%s",
            conversation.id,
            message.id,
            exc_info=True,
        )


def _render_personalization(body: str, personalization: dict | None) -> str:
    """Render personalization variables in message body."""
    if not personalization:
        return body
    rendered = body
    for key, value in personalization.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
    return rendered


def _build_reply_subject(current_subject: str | None, base_subject: str | None) -> str | None:
    if base_subject:
        subject = base_subject.strip()
    else:
        subject = (current_subject or "").strip()
    if not subject:
        return current_subject
    if subject.lower().startswith("re:"):
        return subject
    return f"Re: {subject}"


def _get_rate_limit_per_minute(config) -> int:
    if not config or not isinstance(getattr(config, "metadata_", None), dict):
        return 0
    raw = config.metadata_.get("rate_limit_per_minute")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(value, 0)


def _enforce_rate_limit(channel_type: ChannelType, target: IntegrationTarget | None, config) -> None:
    limit = _get_rate_limit_per_minute(config)
    if limit <= 0:
        return
    key = build_rate_limit_key(channel_type.value, str(target.id) if target else None)
    try:
        check_rate_limit(key, limit)
    except RateLimitExceeded as exc:
        raise TransientOutboundError(
            f"Rate limit exceeded (retry_after={exc.retry_after}s)",
            status_code=429,
        )


def _merge_reply_metadata(reply_context: dict | None) -> dict | None:
    if not reply_context:
        return None
    metadata = {}
    reply_payload = reply_context.get("metadata") if isinstance(reply_context, dict) else None
    if isinstance(reply_payload, dict):
        metadata["reply_to"] = reply_payload
    return metadata or None


def _resolve_person_channel_for_message(
    db: Session,
    person: Person,
    channel_type: ChannelType,
) -> PersonChannel | None:
    """Resolve the recipient channel for outbound messaging."""
    try:
        person_channel_type = PersonChannelType(channel_type.value)
    except Exception:
        return None

    return (
        db.query(PersonChannel)
        .filter(PersonChannel.person_id == person.id)
        .filter(PersonChannel.channel_type == person_channel_type)
        .order_by(PersonChannel.is_primary.desc(), PersonChannel.created_at.desc())
        .first()
    )


def _resolve_reply_author(db: Session, conversation: Conversation, message: Message) -> str:
    if message.direction == MessageDirection.inbound:
        contact = conversation.contact
        if contact:
            return contact.display_name or contact.email or contact.phone or "Contact"
        return "Contact"
    if message.direction == MessageDirection.internal:
        return "Internal Note"
    if message.author_id:
        from app.models.person import Person

        person = db.get(Person, message.author_id)
        if person:
            full_name = (
                person.display_name or " ".join(part for part in [person.first_name, person.last_name] if part).strip()
            )
            return full_name or "Agent"
    return "Agent"


def _build_reply_context(
    db: Session,
    conversation: Conversation,
    reply_to_message_id,
) -> dict | None:
    if not reply_to_message_id:
        return None
    reply_msg = db.get(Message, reply_to_message_id)
    if not reply_msg or reply_msg.conversation_id != conversation.id:
        return None

    timestamp = reply_msg.sent_at or reply_msg.received_at or reply_msg.created_at
    excerpt = (reply_msg.body or "").strip()
    if len(excerpt) > 240:
        excerpt = excerpt[:237].rstrip() + "..."

    reply_metadata = {
        "id": str(reply_msg.id),
        "author": _resolve_reply_author(db, conversation, reply_msg),
        "excerpt": excerpt,
        "sent_at": timestamp.isoformat() if timestamp else None,
        "direction": reply_msg.direction.value,
        "channel_type": reply_msg.channel_type.value if reply_msg.channel_type else None,
    }

    email_in_reply_to = None
    email_references = None
    if reply_msg.channel_type == ChannelType.email and reply_msg.external_id:
        email_in_reply_to = reply_msg.external_id
        existing_refs = None
        if isinstance(reply_msg.metadata_, dict):
            existing_refs = reply_msg.metadata_.get("references")
        if existing_refs:
            existing_refs = str(existing_refs)
            if email_in_reply_to not in existing_refs:
                email_references = f"{existing_refs} {email_in_reply_to}"
            else:
                email_references = existing_refs
        else:
            email_references = email_in_reply_to

    return {
        "reply_to_message_id": reply_msg.id,
        "metadata": reply_metadata,
        "email_in_reply_to": email_in_reply_to,
        "email_references": email_references,
        "whatsapp_reply_message_id": reply_msg.external_id if reply_msg.channel_type == ChannelType.whatsapp else None,
    }


def _resolve_reply_context(
    db: Session,
    reply_to_message_id,
    channel_type: ChannelType,
    target: IntegrationTarget | None,
) -> dict | None:
    """Resolve and validate reply context for outbound messages."""
    if not reply_to_message_id:
        return None
    reply_msg = db.get(Message, reply_to_message_id)
    if not reply_msg:
        return None
    if reply_msg.channel_type and reply_msg.channel_type != channel_type:
        raise InboxValidationError(
            "reply_channel_mismatch",
            "Reply channel does not match the originating channel",
        )
    if target and reply_msg.channel_target_id and reply_msg.channel_target_id != target.id:
        raise InboxValidationError(
            "reply_target_mismatch",
            "Reply channel target does not match the originating target",
        )
    conversation = db.get(Conversation, reply_msg.conversation_id)
    if not conversation:
        return None
    return _build_reply_context(db, conversation, reply_to_message_id)


def _prepare_email_attachments(attachments: list[dict] | None) -> list[dict] | None:
    if not attachments:
        return None
    prepared: list[dict] = []
    for item in attachments:
        if not isinstance(item, dict):
            continue
        if item.get("content_base64"):
            prepared.append(item)
            continue
        stored_name = item.get("stored_name")
        if not stored_name:
            continue
        file_path = Path(settings.message_attachment_upload_dir) / stored_name
        content = None
        try:
            content = file_path.read_bytes()
        except Exception:
            logger.debug("Failed to read attachment bytes: %s", file_path, exc_info=True)
        if content is None:
            continue
        prepared.append(
            {
                "file_name": item.get("file_name") or stored_name,
                "mime_type": item.get("mime_type") or "application/octet-stream",
                "content_base64": base64.b64encode(content).decode("ascii"),
            }
        )
    return prepared or None


def _set_message_send_error(
    message: Message,
    channel: str,
    error: str,
    status_code: int | None = None,
    response_text: str | None = None,
) -> None:
    """Set error metadata on a failed message."""
    metadata = message.metadata_ if isinstance(message.metadata_, dict) else {}
    error_payload: dict[str, object] = {
        "channel": channel,
        "error": error,
    }
    if status_code is not None:
        error_payload["status_code"] = status_code
    if response_text:
        error_payload["response_text"] = response_text[:500]
    metadata["send_error"] = error_payload
    message.metadata_ = metadata


def _get_last_inbound_message(db: Session, conversation_id) -> Message | None:
    """Get the last inbound message for a conversation."""
    return (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .filter(Message.direction == MessageDirection.inbound)
        .order_by(func.coalesce(Message.received_at, Message.created_at).desc())
        .first()
    )


def _get_last_inbound_message_for_channel(db: Session, conversation_id, channel_type: ChannelType) -> Message | None:
    """Get the last inbound message for a conversation and channel."""
    return (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .filter(Message.direction == MessageDirection.inbound)
        .filter(Message.channel_type == channel_type)
        .order_by(func.coalesce(Message.received_at, Message.created_at).desc())
        .first()
    )


def _resolve_meta_account_id(
    db: Session,
    conversation_id,
    channel_type: ChannelType,
) -> str | None:
    """Resolve Meta page/account ID from conversation messages."""
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


# -----------------------------------------------------------------------------
# Channel-specific sending functions
# -----------------------------------------------------------------------------


def _send_email_message(
    db: Session,
    conversation: Conversation,
    person_channel: PersonChannel,
    target: IntegrationTarget | None,
    config: ConnectorConfig | None,
    payload: InboxSendRequest,
    rendered_body: str,
    author_id: str | None,
    reply_context: dict | None,
    raise_on_failure: bool = False,
) -> Message:
    """Send a message via email channel."""
    if not person_channel.address:
        raise InboxValidationError("recipient_missing", "Recipient email missing")
    _enforce_rate_limit(payload.channel_type, target, config)

    message = conversation_service.Messages.create(
        db,
        MessageCreate(
            conversation_id=conversation.id,
            person_channel_id=person_channel.id,
            channel_target_id=target.id if target else None,
            reply_to_message_id=reply_context["reply_to_message_id"] if reply_context else None,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.queued,
            subject=payload.subject,
            body=rendered_body,
            metadata_=_merge_reply_metadata(reply_context),
            author_id=coerce_uuid(author_id) if author_id else None,
            sent_at=_now(),
        ),
    )

    sent = False
    smtp_debug: dict | None = None
    email_attachments = _prepare_email_attachments(payload.attachments)
    if config:
        smtp_config = _smtp_config_from_connector(config)
        if smtp_config:
            result = email_service.send_email_with_config(
                smtp_config,
                person_channel.address,
                payload.subject or "Message",
                rendered_body,
                rendered_body,
                in_reply_to=reply_context.get("email_in_reply_to") if reply_context else None,
                references=reply_context.get("email_references") if reply_context else None,
                attachments=email_attachments,
            )
            if isinstance(result, tuple):
                sent, smtp_debug = result
            else:
                sent = bool(result)
                smtp_debug = None

    if not sent:
        result = email_service.send_email(
            db,
            person_channel.address,
            payload.subject or "Message",
            rendered_body,
            rendered_body,
            in_reply_to=reply_context.get("email_in_reply_to") if reply_context else None,
            references=reply_context.get("email_references") if reply_context else None,
            attachments=email_attachments,
        )
        if isinstance(result, tuple):
            sent, smtp_debug = result
        else:
            sent = bool(result)
            smtp_debug = None

    message.status = MessageStatus.sent if sent else MessageStatus.failed
    if smtp_debug:
        metadata = message.metadata_ if isinstance(message.metadata_, dict) else {}
        metadata["smtp_debug"] = smtp_debug
        message.metadata_ = metadata
    db.commit()
    db.refresh(message)
    inbox_cache.invalidate_inbox_list()
    _broadcast_outbound_summary(db, conversation, message)

    from app.websocket.broadcaster import broadcast_message_status

    broadcast_message_status(str(message.id), str(message.conversation_id), message.status.value)

    if raise_on_failure and not sent:
        raise TransientOutboundError("Email send failed")

    return message


def _send_whatsapp_message(
    db: Session,
    conversation: Conversation,
    person_channel: PersonChannel,
    target: IntegrationTarget | None,
    config: ConnectorConfig | None,
    payload: InboxSendRequest,
    rendered_body: str,
    author_id: str | None,
    reply_context: dict | None,
    raise_on_failure: bool = False,
) -> Message:
    """Send a message via WhatsApp channel."""
    if not config:
        raise InboxConfigError("whatsapp_config_missing", "WhatsApp connector not configured")
    _enforce_rate_limit(payload.channel_type, target, config)

    token = None
    if config.auth_config:
        token = config.auth_config.get("token") or config.auth_config.get("access_token")
    if not token:
        raise InboxConfigError("whatsapp_token_missing", "WhatsApp access token missing")

    phone_number_id = None
    if config.metadata_:
        phone_number_id = config.metadata_.get("phone_number_id")
    if config.auth_config and not phone_number_id:
        phone_number_id = config.auth_config.get("phone_number_id")
    if not phone_number_id:
        raise InboxConfigError("whatsapp_phone_number_missing", "WhatsApp phone_number_id missing")

    display_body = rendered_body
    reply_metadata = _merge_reply_metadata(reply_context)
    if payload.whatsapp_template_name:
        display_body = rendered_body or f"[Template] {payload.whatsapp_template_name}"
        reply_metadata = dict(reply_metadata or {})
        reply_metadata["whatsapp_template"] = {
            "name": payload.whatsapp_template_name,
            "language": payload.whatsapp_template_language,
            "components": payload.whatsapp_template_components or [],
        }

    message = conversation_service.Messages.create(
        db,
        MessageCreate(
            conversation_id=conversation.id,
            person_channel_id=person_channel.id,
            channel_target_id=target.id if target else None,
            reply_to_message_id=reply_context["reply_to_message_id"] if reply_context else None,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.queued,
            subject=payload.subject,
            body=display_body,
            metadata_=reply_metadata,
            author_id=coerce_uuid(author_id) if author_id else None,
            sent_at=_now(),
        ),
    )

    attachments = payload.attachments or []
    media_payload = None
    if attachments:
        first_attachment = attachments[0] if isinstance(attachments, list) else None
        if isinstance(first_attachment, dict):
            attachment_url = first_attachment.get("url") or ""
            if attachment_url and not attachment_url.startswith(("http://", "https://")):
                app_url = email_service._get_app_url(db).rstrip("/")
                if app_url:
                    attachment_url = f"{app_url}{attachment_url}"
            mime_type = (first_attachment.get("mime_type") or "").lower()
            if attachment_url:
                if mime_type.startswith("image/"):
                    media_payload = {
                        "type": "image",
                        "image": {
                            "link": attachment_url,
                            **({"caption": rendered_body} if rendered_body else {}),
                        },
                    }
                else:
                    media_payload = {
                        "type": "document",
                        "document": {
                            "link": attachment_url,
                            **(
                                {"filename": first_attachment.get("file_name")}
                                if first_attachment.get("file_name")
                                else {}
                            ),
                            **({"caption": rendered_body} if rendered_body else {}),
                        },
                    }

    base_url = config.base_url or "https://graph.facebook.com/v19.0"
    if payload.whatsapp_template_name:
        template_payload: dict[str, Any] = {
            "name": payload.whatsapp_template_name,
            "language": {"code": payload.whatsapp_template_language},
        }
        if payload.whatsapp_template_components:
            template_payload["components"] = payload.whatsapp_template_components
        payload_data = {
            "messaging_product": "whatsapp",
            "to": person_channel.address,
            "type": "template",
            "template": template_payload,
        }
    elif media_payload:
        payload_data = {
            "messaging_product": "whatsapp",
            "to": person_channel.address,
            **media_payload,
        }
    else:
        payload_data = {
            "messaging_product": "whatsapp",
            "to": person_channel.address,
            "type": "text",
            "text": {"body": rendered_body},
        }
    if reply_context and reply_context.get("whatsapp_reply_message_id"):
        payload_data["context"] = {"message_id": reply_context["whatsapp_reply_message_id"]}
    headers = {"Authorization": f"Bearer {token}"}
    if config.headers:
        headers.update(config.headers)

    retry_error: OutboundSendError | None = None
    try:
        whatsapp_timeout = config.timeout_sec or _get_whatsapp_api_timeout(db)

        def _do_call():
            response = httpx.post(
                f"{base_url.rstrip('/')}/{phone_number_id}/messages",
                json=payload_data,
                headers=headers,
                timeout=whatsapp_timeout,
            )
            response.raise_for_status()
            return response

        response = WHATSAPP_CIRCUIT.call(_do_call)
        data = response.json() if response.content else {}
        message.status = MessageStatus.sent
        _store_external_message_id(message, data.get("messages", [{}])[0].get("id"))
    except CircuitOpenError as exc:
        message.status = MessageStatus.failed
        _set_message_send_error(message, "whatsapp", str(exc))
        if raise_on_failure:
            retry_error = TransientOutboundError("WhatsApp circuit open")
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
        if raise_on_failure:
            if status_code in (401, 403):
                retry_error = PermanentOutboundError(
                    f"WhatsApp send auth failed (status={status_code})",
                    status_code=401,
                )
            elif _is_transient_exception(exc, status_code=status_code):
                retry_error = TransientOutboundError(f"WhatsApp send failed (status={status_code})")
            else:
                retry_error = PermanentOutboundError(f"WhatsApp send failed (status={status_code})")

    db.commit()
    db.refresh(message)
    inbox_cache.invalidate_inbox_list()
    _broadcast_outbound_summary(db, conversation, message)

    from app.websocket.broadcaster import broadcast_message_status

    broadcast_message_status(str(message.id), str(message.conversation_id), message.status.value)

    if retry_error:
        raise retry_error

    return message


def _send_facebook_message(
    db: Session,
    conversation: Conversation,
    person_channel: PersonChannel,
    target: IntegrationTarget | None,
    last_inbound: Message | None,
    payload: InboxSendRequest,
    rendered_body: str,
    author_id: str | None,
    reply_context: dict | None,
    raise_on_failure: bool = False,
) -> Message:
    """Send a message via Facebook Messenger channel."""
    from app.services import meta_messaging

    _enforce_rate_limit(payload.channel_type, target, target.connector_config if target else None)

    account_id = _resolve_meta_account_id(db, conversation.id, payload.channel_type)

    # Check Meta 24-hour reply window
    if not last_inbound or not last_inbound.received_at:
        raise InboxValidationError("meta_reply_window_expired", "Meta reply window expired")
    if (datetime.now(UTC) - last_inbound.received_at).total_seconds() > 24 * 3600:
        raise InboxValidationError("meta_reply_window_expired", "Meta reply window expired")

    message = conversation_service.Messages.create(
        db,
        MessageCreate(
            conversation_id=conversation.id,
            person_channel_id=person_channel.id,
            channel_target_id=target.id if target else None,
            reply_to_message_id=reply_context["reply_to_message_id"] if reply_context else None,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.queued,
            body=rendered_body,
            metadata_=_merge_reply_metadata(reply_context),
            author_id=coerce_uuid(author_id) if author_id else None,
            sent_at=_now(),
        ),
    )

    retry_error: OutboundSendError | None = None
    try:
        result = META_CIRCUIT.call(
            meta_messaging.send_facebook_message_sync,
            db,
            person_channel.address,
            rendered_body,
            target,
            account_id=account_id,
        )
        message.status = MessageStatus.sent
        _store_external_message_id(message, result.get("message_id"))
    except CircuitOpenError as exc:
        message.status = MessageStatus.failed
        _set_message_send_error(message, "facebook_messenger", str(exc))
        if raise_on_failure:
            retry_error = TransientOutboundError("Facebook circuit open")
    except Exception as exc:
        logger.error(
            "facebook_messenger_send_failed conversation_id=%s error=%s",
            conversation.id,
            exc,
        )
        message.status = MessageStatus.failed
        _set_message_send_error(message, "facebook_messenger", str(exc))
        if raise_on_failure:
            if _is_transient_exception(exc):
                retry_error = TransientOutboundError("Facebook send failed")
            else:
                retry_error = PermanentOutboundError("Facebook send failed")

    db.commit()
    db.refresh(message)
    inbox_cache.invalidate_inbox_list()
    _broadcast_outbound_summary(db, conversation, message)

    from app.websocket.broadcaster import broadcast_message_status

    broadcast_message_status(str(message.id), str(message.conversation_id), message.status.value)

    if retry_error:
        raise retry_error

    return message


def _send_instagram_message(
    db: Session,
    conversation: Conversation,
    person_channel: PersonChannel,
    target: IntegrationTarget | None,
    last_inbound: Message | None,
    payload: InboxSendRequest,
    rendered_body: str,
    author_id: str | None,
    reply_context: dict | None,
    raise_on_failure: bool = False,
) -> Message:
    """Send a message via Instagram DM channel."""
    from app.services import meta_messaging

    _enforce_rate_limit(payload.channel_type, target, target.connector_config if target else None)

    account_id = _resolve_meta_account_id(db, conversation.id, payload.channel_type)

    # Check Meta 24-hour reply window
    if not last_inbound or not last_inbound.received_at:
        raise InboxValidationError("meta_reply_window_expired", "Meta reply window expired")
    if (datetime.now(UTC) - last_inbound.received_at).total_seconds() > 24 * 3600:
        raise InboxValidationError("meta_reply_window_expired", "Meta reply window expired")

    message = conversation_service.Messages.create(
        db,
        MessageCreate(
            conversation_id=conversation.id,
            person_channel_id=person_channel.id,
            channel_target_id=target.id if target else None,
            reply_to_message_id=reply_context["reply_to_message_id"] if reply_context else None,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.queued,
            body=rendered_body,
            metadata_=_merge_reply_metadata(reply_context),
            author_id=coerce_uuid(author_id) if author_id else None,
            sent_at=_now(),
        ),
    )

    retry_error: OutboundSendError | None = None
    try:
        result = META_CIRCUIT.call(
            meta_messaging.send_instagram_message_sync,
            db,
            person_channel.address,
            rendered_body,
            target,
            account_id=account_id,
        )
        message.status = MessageStatus.sent
        _store_external_message_id(message, result.get("message_id"))
    except CircuitOpenError as exc:
        message.status = MessageStatus.failed
        _set_message_send_error(message, "instagram_dm", str(exc))
        if raise_on_failure:
            retry_error = TransientOutboundError("Instagram circuit open")
    except httpx.HTTPStatusError as exc:
        message.status = MessageStatus.failed
        status_code = exc.response.status_code if exc.response is not None else None
        response_text = exc.response.text if exc.response is not None else None
        _set_message_send_error(
            message,
            "instagram_dm",
            str(exc),
            status_code=status_code,
            response_text=response_text,
        )
        if isinstance(message.metadata_, dict) and message.metadata_.get("send_error"):
            # Preserve raw Meta error body for debugging.
            message.metadata_["send_error"]["meta_error"] = response_text or ""
        if raise_on_failure:
            if _is_transient_exception(exc, status_code=status_code):
                retry_error = TransientOutboundError("Instagram send failed")
            else:
                retry_error = PermanentOutboundError("Instagram send failed")
    except Exception as exc:
        logger.error(
            "instagram_dm_send_failed conversation_id=%s error=%s",
            conversation.id,
            exc,
        )
        message.status = MessageStatus.failed
        _set_message_send_error(message, "instagram_dm", str(exc))
        if raise_on_failure:
            if _is_transient_exception(exc):
                retry_error = TransientOutboundError("Instagram send failed")
            else:
                retry_error = PermanentOutboundError("Instagram send failed")

    db.commit()
    db.refresh(message)
    inbox_cache.invalidate_inbox_list()
    _broadcast_outbound_summary(db, conversation, message)

    from app.websocket.broadcaster import broadcast_message_status

    broadcast_message_status(str(message.id), str(message.conversation_id), message.status.value)

    if retry_error:
        raise retry_error

    return message


def _send_chat_widget_message(
    db: Session,
    conversation: Conversation,
    person_channel: PersonChannel,
    target: IntegrationTarget | None,
    payload: InboxSendRequest,
    rendered_body: str,
    author_id: str | None,
    reply_context: dict | None,
    trace_id: str | None,
) -> Message:
    """Send a message via chat widget channel.

    Chat widget messages are delivered via WebSocket to the visitor.
    """
    message = conversation_service.Messages.create(
        db,
        MessageCreate(
            conversation_id=conversation.id,
            person_channel_id=person_channel.id,
            channel_target_id=target.id if target else None,
            reply_to_message_id=reply_context["reply_to_message_id"] if reply_context else None,
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=rendered_body,
            metadata_=_merge_reply_metadata(reply_context),
            author_id=coerce_uuid(author_id) if author_id else None,
            sent_at=_now(),
        ),
    )

    db.commit()
    db.refresh(message)
    inbox_cache.invalidate_inbox_list()
    _broadcast_outbound_summary(db, conversation, message)

    # Broadcast to admin subscribers
    from app.websocket.broadcaster import (
        broadcast_message_status,
        broadcast_new_message,
        broadcast_to_widget_visitor,
    )

    broadcast_new_message(message, conversation)
    broadcast_message_status(str(message.id), str(message.conversation_id), message.status.value)

    # Broadcast to widget visitor via their WebSocket
    from app.models.crm.chat_widget import WidgetVisitorSession

    widget_session = (
        db.query(WidgetVisitorSession).filter(WidgetVisitorSession.conversation_id == conversation.id).first()
    )
    if widget_session:
        broadcast_to_widget_visitor(str(widget_session.id), message)

    logger.info(
        "webchat_message_sent trace_id=%s message_id=%s conversation_id=%s",
        trace_id,
        message.id,
        conversation.id,
    )

    return message




# -----------------------------------------------------------------------------
# Main send_message function
# -----------------------------------------------------------------------------


def send_message(
    db: Session,
    payload: InboxSendRequest,
    author_id: str | None = None,
    *,
    raise_on_failure: bool = False,
    trace_id: str | None = None,
) -> Message:
    """Send a message through the specified channel.

    Args:
        db: Database session
        payload: Send request with conversation, channel, and message details
        author_id: Optional ID of the person sending the message

    Returns:
        Message: The created message record

    Raises:
        InboxError: If channel is not configured or message cannot be sent
    """
    try:
        conversation = conversation_service.Conversations.get(db, str(payload.conversation_id))
        person = conversation.person
        if not person:
            raise InboxNotFoundError("contact_not_found", "Contact not found")

        if payload.template_id and not payload.body:
            template = message_templates.get(db, str(payload.template_id))
            payload = payload.model_copy(
                update={
                    "body": template.body,
                    "subject": payload.subject or template.subject,
                    "channel_type": template.channel_type,
                }
            )

        last_inbound = _get_last_inbound_message(db, conversation.id)
        resolved_channel_target_id = payload.channel_target_id

        if USE_INBOX_LOGIC_SERVICE:
            requested_channel_type: LogicChannelType = typing_cast(LogicChannelType, payload.channel_type.value)
            last_inbound_channel_type: LogicChannelType | None = typing_cast(
                LogicChannelType | None,
                last_inbound.channel_type.value if last_inbound and last_inbound.channel_type else None,
            )
            ctx = MessageContext(
                conversation_id=str(conversation.id),
                person_id=str(person.id),
                requested_channel_type=requested_channel_type,
                requested_channel_target_id=str(payload.channel_target_id) if payload.channel_target_id else None,
                last_inbound_channel_type=last_inbound_channel_type,
                last_inbound_channel_target_id=str(last_inbound.channel_target_id)
                if last_inbound and last_inbound.channel_target_id
                else None,
                last_inbound_received_at_iso=last_inbound.received_at.isoformat()
                if last_inbound and last_inbound.received_at
                else None,
                now_iso=_now().isoformat(),
            )
            decision = _logic_service.decide_send_message(ctx)
            if decision.status == "deny":
                raise InboxValidationError("message_not_allowed", decision.reason or "Message not allowed")

            if decision.channel_type != payload.channel_type.value:
                payload = payload.model_copy(update={"channel_type": ChannelType(decision.channel_type)})

            if decision.channel_target_id and not payload.channel_target_id:
                resolved_channel_target_id = coerce_uuid(decision.channel_target_id)
        else:
            if last_inbound and last_inbound.channel_type != payload.channel_type:
                raise InboxValidationError(
                    "reply_channel_mismatch",
                    "Reply channel does not match the originating channel",
                )

            if last_inbound and last_inbound.channel_target_id:
                if resolved_channel_target_id and last_inbound.channel_target_id != resolved_channel_target_id:
                    raise InboxValidationError(
                        "reply_target_mismatch",
                        "Reply channel target does not match the originating target",
                    )
                resolved_channel_target_id = last_inbound.channel_target_id

        person_channel = _resolve_person_channel_for_message(db, person, payload.channel_type)
        if not person_channel:
            raise InboxValidationError("contact_channel_missing", "Contact channel not found")

        target = None
        if resolved_channel_target_id:
            target = _resolve_integration_target(
                db,
                payload.channel_type,
                str(resolved_channel_target_id),
            )
        if not target:
            target = _resolve_integration_target(db, payload.channel_type, None)

        config = _resolve_connector_config(db, target, payload.channel_type) if target else None
        reply_context = None
        if payload.reply_to_message_id:
            reply_context = _resolve_reply_context(
                db,
                payload.reply_to_message_id,
                payload.channel_type,
                target,
            )
        if payload.channel_type == ChannelType.email:
            last_inbound_email = (
                last_inbound
                if last_inbound and last_inbound.channel_type == ChannelType.email
                else _get_last_inbound_message_for_channel(db, conversation.id, ChannelType.email)
            )
            reply_subject = None
            if payload.reply_to_message_id:
                reply_msg = db.get(Message, payload.reply_to_message_id)
                if reply_msg and reply_msg.subject:
                    reply_subject = reply_msg.subject
            if not reply_subject and last_inbound_email and last_inbound_email.subject:
                reply_subject = last_inbound_email.subject
            if reply_subject:
                payload = payload.model_copy(update={"subject": _build_reply_subject(payload.subject, reply_subject)})

            if (
                not payload.reply_to_message_id
                and not reply_context
                and last_inbound_email
                and (
                    not target
                    or not last_inbound_email.channel_target_id
                    or last_inbound_email.channel_target_id == target.id
                )
            ):
                reply_context = _build_reply_context(db, conversation, last_inbound_email.id)

        rendered_body = _render_personalization(payload.body or "", payload.personalization)

        if payload.channel_type == ChannelType.email:
            return _send_email_message(
                db,
                conversation,
                person_channel,
                target,
                config,
                payload,
                rendered_body,
                author_id,
                reply_context,
                raise_on_failure=raise_on_failure,
            )

        if payload.channel_type == ChannelType.whatsapp:
            return _send_whatsapp_message(
                db,
                conversation,
                person_channel,
                target,
                config,
                payload,
                rendered_body,
                author_id,
                reply_context,
                raise_on_failure=raise_on_failure,
            )

        if payload.channel_type == ChannelType.facebook_messenger:
            return _send_facebook_message(
                db,
                conversation,
                person_channel,
                target,
                last_inbound,
                payload,
                rendered_body,
                author_id,
                reply_context,
                raise_on_failure=raise_on_failure,
            )

        if payload.channel_type == ChannelType.instagram_dm:
            return _send_instagram_message(
                db,
                conversation,
                person_channel,
                target,
                last_inbound,
                payload,
                rendered_body,
                author_id,
                reply_context,
                raise_on_failure=raise_on_failure,
            )

        if payload.channel_type == ChannelType.chat_widget:
            return _send_chat_widget_message(
                db,
                conversation,
                person_channel,
                target,
                payload,
                rendered_body,
                author_id,
                reply_context,
                trace_id,
            )

        raise InboxValidationError("channel_unsupported", "Unsupported channel type")
    except InboxError as exc:
        raise exc.to_http_exception() from exc


def send_message_with_retry(
    db: Session,
    payload: InboxSendRequest,
    author_id: str | None = None,
    trace_id: str | None = None,
    *,
    max_attempts: int = 3,
    base_backoff: float = 0.5,
    max_backoff: float = 2.0,
) -> Message:
    """Send a message with retry/backoff for transient failures."""
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    channel_label = "unknown"
    if payload and payload.channel_type:
        channel_label = payload.channel_type.value
    start = time.perf_counter()
    last_exc: TransientOutboundError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            message = send_message(
                db,
                payload,
                author_id=author_id,
                raise_on_failure=True,
                trace_id=trace_id,
            )
            OUTBOUND_MESSAGES.labels(channel_type=channel_label, status="sent").inc()
            MESSAGE_PROCESSING_TIME.labels(channel_type=channel_label, direction="outbound").observe(
                time.perf_counter() - start
            )
            return message
        except TransientOutboundError as exc:
            last_exc = exc
            OUTBOUND_MESSAGES.labels(channel_type=channel_label, status="retried").inc()
            if attempt >= max_attempts:
                OUTBOUND_MESSAGES.labels(channel_type=channel_label, status="failed").inc()
                MESSAGE_PROCESSING_TIME.labels(channel_type=channel_label, direction="outbound").observe(
                    time.perf_counter() - start
                )
                raise
            _sleep_with_backoff(attempt, base_backoff, max_backoff)
        except PermanentOutboundError:
            OUTBOUND_MESSAGES.labels(channel_type=channel_label, status="failed").inc()
            MESSAGE_PROCESSING_TIME.labels(channel_type=channel_label, direction="outbound").observe(
                time.perf_counter() - start
            )
            raise
        except Exception:
            OUTBOUND_MESSAGES.labels(channel_type=channel_label, status="failed").inc()
            MESSAGE_PROCESSING_TIME.labels(channel_type=channel_label, direction="outbound").observe(
                time.perf_counter() - start
            )
            raise
    MESSAGE_PROCESSING_TIME.labels(channel_type=channel_label, direction="outbound").observe(
        time.perf_counter() - start
    )
    raise last_exc or TransientOutboundError("Outbound send failed")


def send_reply(db: Session, payload: InboxSendRequest, author_id: str | None = None) -> Message:
    return send_message(db, payload, author_id=author_id)


def send_outbound_message(db: Session, payload: InboxSendRequest, author_id: str | None = None) -> Message:
    return send_message(db, payload, author_id=author_id)
