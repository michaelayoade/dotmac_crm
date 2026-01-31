"""Outbound message sending for CRM inbox.

Handles sending messages across email, WhatsApp, Facebook Messenger,
Instagram DM, and chat widget channels.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast as typing_cast

import httpx
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.logic.crm_inbox_logic import (
    ChannelType as LogicChannelType,
    LogicService,
    MessageContext,
)
from app.models.crm.conversation import Message
from app.models.crm.enums import ChannelType, MessageDirection, MessageStatus
from app.models.person import ChannelType as PersonChannelType, PersonChannel
from app.schemas.crm.conversation import MessageCreate
from app.schemas.crm.inbox import InboxSendRequest
from app.services import email as email_service
from app.services.common import coerce_uuid
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox_connectors import (
    _get_whatsapp_api_timeout,
    _resolve_connector_config,
    _resolve_integration_target,
    _smtp_config_from_connector,
)
from app.services.crm.inbox_normalizers import _normalize_email_address

if TYPE_CHECKING:
    from app.models.crm.conversation import Conversation
    from app.models.crm.inbox import ConnectorConfig, IntegrationTarget

logger = get_logger(__name__)
USE_INBOX_LOGIC_SERVICE = os.getenv("USE_INBOX_LOGIC_SERVICE", "0") == "1"
_logic_service = LogicService()


def _now():
    return datetime.now(timezone.utc)


def _render_personalization(body: str, personalization: dict | None) -> str:
    """Render personalization variables in message body."""
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
    conversation: "Conversation",
    person_channel: PersonChannel,
    target: "IntegrationTarget | None",
    config: "ConnectorConfig | None",
    payload: InboxSendRequest,
    rendered_body: str,
    author_id: str | None,
) -> Message:
    """Send a message via email channel."""
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

    broadcast_message_status(
        str(message.id), str(message.conversation_id), message.status.value
    )

    return message


def _send_whatsapp_message(
    db: Session,
    conversation: "Conversation",
    person_channel: PersonChannel,
    target: "IntegrationTarget | None",
    config: "ConnectorConfig | None",
    payload: InboxSendRequest,
    rendered_body: str,
    author_id: str | None,
) -> Message:
    """Send a message via WhatsApp channel."""
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

    broadcast_message_status(
        str(message.id), str(message.conversation_id), message.status.value
    )

    return message


def _send_facebook_message(
    db: Session,
    conversation: "Conversation",
    person_channel: PersonChannel,
    target: "IntegrationTarget | None",
    last_inbound: Message | None,
    payload: InboxSendRequest,
    rendered_body: str,
    author_id: str | None,
) -> Message:
    """Send a message via Facebook Messenger channel."""
    from app.services import meta_messaging

    account_id = _resolve_meta_account_id(db, conversation.id, payload.channel_type)

    # Check Meta 24-hour reply window
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
        logger.error(
            "facebook_messenger_send_failed conversation_id=%s error=%s",
            conversation.id,
            exc,
        )
        message.status = MessageStatus.failed
        _set_message_send_error(message, "facebook_messenger", str(exc))

    db.commit()
    db.refresh(message)

    from app.websocket.broadcaster import broadcast_message_status

    broadcast_message_status(
        str(message.id), str(message.conversation_id), message.status.value
    )

    return message


def _send_instagram_message(
    db: Session,
    conversation: "Conversation",
    person_channel: PersonChannel,
    target: "IntegrationTarget | None",
    last_inbound: Message | None,
    payload: InboxSendRequest,
    rendered_body: str,
    author_id: str | None,
) -> Message:
    """Send a message via Instagram DM channel."""
    from app.services import meta_messaging

    account_id = _resolve_meta_account_id(db, conversation.id, payload.channel_type)

    # Check Meta 24-hour reply window
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
        logger.error(
            "instagram_dm_send_failed conversation_id=%s error=%s",
            conversation.id,
            exc,
        )
        message.status = MessageStatus.failed
        _set_message_send_error(message, "instagram_dm", str(exc))

    db.commit()
    db.refresh(message)

    from app.websocket.broadcaster import broadcast_message_status

    broadcast_message_status(
        str(message.id), str(message.conversation_id), message.status.value
    )

    return message


def _send_chat_widget_message(
    db: Session,
    conversation: "Conversation",
    person_channel: PersonChannel,
    target: "IntegrationTarget | None",
    payload: InboxSendRequest,
    rendered_body: str,
    author_id: str | None,
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
            channel_type=payload.channel_type,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=rendered_body,
            author_id=coerce_uuid(author_id) if author_id else None,
            sent_at=_now(),
        ),
    )

    db.commit()
    db.refresh(message)

    # Broadcast to admin subscribers
    from app.websocket.broadcaster import (
        broadcast_message_status,
        broadcast_new_message,
        broadcast_to_widget_visitor,
    )

    broadcast_new_message(message, conversation)
    broadcast_message_status(
        str(message.id), str(message.conversation_id), message.status.value
    )

    # Broadcast to widget visitor via their WebSocket
    from app.models.crm.chat_widget import WidgetVisitorSession

    widget_session = (
        db.query(WidgetVisitorSession)
        .filter(WidgetVisitorSession.conversation_id == conversation.id)
        .first()
    )
    if widget_session:
        broadcast_to_widget_visitor(str(widget_session.id), message)

    return message


# -----------------------------------------------------------------------------
# Main send_message function
# -----------------------------------------------------------------------------


def send_message(
    db: Session, payload: InboxSendRequest, author_id: str | None = None
) -> Message:
    """Send a message through the specified channel.

    Args:
        db: Database session
        payload: Send request with conversation, channel, and message details
        author_id: Optional ID of the person sending the message

    Returns:
        Message: The created message record

    Raises:
        HTTPException: If channel is not configured or message cannot be sent
    """
    conversation = conversation_service.Conversations.get(
        db, str(payload.conversation_id)
    )
    person = conversation.person
    if not person:
        raise HTTPException(status_code=404, detail="Contact not found")

    last_inbound = _get_last_inbound_message(db, conversation.id)
    resolved_channel_target_id = payload.channel_target_id

    if USE_INBOX_LOGIC_SERVICE:
        requested_channel_type: LogicChannelType = typing_cast(
            LogicChannelType, payload.channel_type.value
        )
        last_inbound_channel_type: LogicChannelType | None = typing_cast(
            LogicChannelType | None,
            last_inbound.channel_type.value
            if last_inbound and last_inbound.channel_type
            else None,
        )
        ctx = MessageContext(
            conversation_id=str(conversation.id),
            person_id=str(person.id),
            requested_channel_type=requested_channel_type,
            requested_channel_target_id=str(payload.channel_target_id)
            if payload.channel_target_id
            else None,
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
            raise HTTPException(
                status_code=400, detail=decision.reason or "Message not allowed"
            )

        if decision.channel_type != payload.channel_type.value:
            payload = payload.model_copy(
                update={"channel_type": ChannelType(decision.channel_type)}
            )

        if decision.channel_target_id and not payload.channel_target_id:
            resolved_channel_target_id = coerce_uuid(decision.channel_target_id)
    else:
        if last_inbound and last_inbound.channel_type != payload.channel_type:
            raise HTTPException(
                status_code=400,
                detail="Reply channel does not match the originating channel",
            )

        if last_inbound and last_inbound.channel_target_id:
            if (
                resolved_channel_target_id
                and last_inbound.channel_target_id != resolved_channel_target_id
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Reply channel target does not match the originating channel",
                )
            if not resolved_channel_target_id:
                resolved_channel_target_id = last_inbound.channel_target_id

    # Auto-create email channel if needed
    if payload.channel_type == ChannelType.email and not payload.person_channel_id:
        email_address = (
            _normalize_email_address(person.email) if person.email else None
        )
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

    # Resolve person channel
    person_channel = None
    if payload.person_channel_id:
        person_channel = db.get(PersonChannel, payload.person_channel_id)
        if not person_channel:
            raise HTTPException(status_code=400, detail="Contact channel not found")
        if person_channel.person_id != person.id:
            raise HTTPException(status_code=400, detail="Contact channel mismatch")
        if person_channel.channel_type.value != payload.channel_type.value:
            raise HTTPException(
                status_code=400, detail="Contact channel type mismatch"
            )
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
    config = (
        _resolve_connector_config(db, target, payload.channel_type) if target else None
    )

    rendered_body = _render_personalization(payload.body, payload.personalization)

    # Handle each channel type
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
        )

    raise HTTPException(status_code=400, detail="Unsupported channel type")
