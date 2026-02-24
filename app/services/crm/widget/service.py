"""Chat widget service layer."""

from __future__ import annotations

import hashlib
import html
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.logging import get_logger
from app.models.crm.chat_widget import ChatWidgetConfig, WidgetVisitorSession
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection, MessageStatus
from app.models.crm.sales import Lead
from app.models.person import ChannelType as PersonChannelType
from app.models.person import PartyStatus, Person, PersonChannel
from app.schemas.crm.chat_widget import (
    BusinessHours,
    BusinessHoursDay,
    ChatWidgetConfigCreate,
    ChatWidgetConfigUpdate,
    ChatWidgetPublicConfig,
    PrechatField,
    WidgetSessionCreate,
)
from app.services.common import coerce_uuid

if TYPE_CHECKING:
    from uuid import UUID

logger = get_logger(__name__)


def is_within_business_hours(business_hours: BusinessHours | dict | None) -> bool:
    """
    Check if current time is within configured business hours.

    Returns True (online) if no restrictions, within hours, or on any error (fail-safe).
    """
    if business_hours is None:
        return True

    try:
        # Normalize input (dict from JSON or Pydantic model)
        if isinstance(business_hours, dict):
            bh = BusinessHours(**business_hours)
        else:
            bh = business_hours

        # Parse timezone (fail-safe to True on invalid)
        try:
            tz = ZoneInfo(bh.timezone)
        except (KeyError, ValueError):
            logger.warning("invalid_business_hours_timezone tz=%s", bh.timezone)
            return True

        now = datetime.now(tz)

        # Get day config (0=Monday, 6=Sunday)
        day_map: dict[int, BusinessHoursDay] = {
            0: bh.monday,
            1: bh.tuesday,
            2: bh.wednesday,
            3: bh.thursday,
            4: bh.friday,
            5: bh.saturday,
            6: bh.sunday,
        }
        day_config = day_map[now.weekday()]

        if not day_config.enabled:
            return False

        # Parse times
        start_h, start_m = map(int, day_config.start.split(":"))
        end_h, end_m = map(int, day_config.end.split(":"))

        start_time = now.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
        end_time = now.replace(hour=end_h, minute=end_m, second=0, microsecond=0)

        # Handle overnight hours (e.g., 22:00 - 06:00)
        if end_time <= start_time:
            return now >= start_time or now < end_time

        return start_time <= now < end_time

    except Exception as exc:
        logger.warning("business_hours_check_error error=%s", exc)
        return True  # Fail-safe to online


def _validate_prechat_settings(enabled: bool, fields: list[PrechatField] | None) -> None:
    if not enabled:
        return
    if not fields:
        raise ValueError("Pre-chat form requires at least one field")
    seen = set()
    has_email = False
    for field in fields:
        if field.name in seen:
            raise ValueError(f"Duplicate pre-chat field name: {field.name}")
        seen.add(field.name)
        if field.field_type == "select" and not field.options:
            raise ValueError(f"Select field '{field.name}' requires options")
        if field.field_type == "email":
            has_email = True
    if not has_email:
        raise ValueError("Pre-chat form must include an email field")


def _now() -> datetime:
    return datetime.now(UTC)


def _generate_visitor_token() -> str:
    """Generate a secure random token for visitor authentication."""
    return secrets.token_urlsafe(48)


def _hash_fingerprint(fingerprint: str | None) -> str | None:
    """Hash the browser fingerprint for storage."""
    if not fingerprint:
        return None
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def _sanitize_message_body(body: str) -> str:
    """Sanitize message body by escaping HTML and limiting length."""
    # Strip HTML tags and escape any remaining
    cleaned = html.escape(body.strip())
    # Limit to 5000 characters
    return cleaned[:5000]


def _extract_domain_from_origin(origin: str) -> str | None:
    """Extract domain from Origin header."""
    if not origin:
        return None
    try:
        parsed = urlparse(origin)
        return parsed.netloc.lower() if parsed.netloc else None
    except Exception:
        return None


def _domain_matches_pattern(domain: str, pattern: str) -> bool:
    """Check if domain matches an allowed pattern (supports wildcards)."""
    pattern = pattern.lower().strip()
    domain = domain.lower().strip()

    if pattern.startswith("*."):
        # Wildcard subdomain match
        base_pattern = pattern[2:]  # Remove *.
        return domain == base_pattern or domain.endswith("." + base_pattern)

    # Exact match
    return domain == pattern


class ChatWidgetConfigManager:
    """Manager for chat widget configurations."""

    @staticmethod
    def create(db: Session, payload: ChatWidgetConfigCreate) -> ChatWidgetConfig:
        """Create a new widget configuration."""
        _validate_prechat_settings(payload.prechat_form_enabled, payload.prechat_fields)
        config = ChatWidgetConfig(
            name=payload.name,
            allowed_domains=payload.allowed_domains,
            primary_color=payload.primary_color,
            bubble_position=payload.bubble_position,
            welcome_message=payload.welcome_message,
            placeholder_text=payload.placeholder_text,
            widget_title=payload.widget_title,
            offline_message=payload.offline_message,
            prechat_form_enabled=payload.prechat_form_enabled,
            prechat_fields=[f.model_dump() for f in payload.prechat_fields] if payload.prechat_fields else None,
            business_hours=payload.business_hours.model_dump() if payload.business_hours else None,
            rate_limit_messages_per_minute=payload.rate_limit_messages_per_minute,
            rate_limit_sessions_per_ip=payload.rate_limit_sessions_per_ip,
            connector_config_id=payload.connector_config_id,
        )
        db.add(config)
        db.commit()
        db.refresh(config)
        logger.info("chat_widget_config_created config_id=%s name=%s", config.id, config.name)
        return config

    @staticmethod
    def get(db: Session, config_id: UUID | str) -> ChatWidgetConfig | None:
        """Get a widget configuration by ID."""
        return db.get(ChatWidgetConfig, coerce_uuid(config_id))

    @staticmethod
    def update(db: Session, config_id: UUID | str, payload: ChatWidgetConfigUpdate) -> ChatWidgetConfig | None:
        """Update a widget configuration."""
        config = db.get(ChatWidgetConfig, coerce_uuid(config_id))
        if not config:
            return None

        update_data = payload.model_dump(exclude_unset=True)
        effective_enabled = update_data.get("prechat_form_enabled", config.prechat_form_enabled)
        effective_fields = update_data.get("prechat_fields", config.prechat_fields)
        if effective_fields is not None and not isinstance(effective_fields, list):
            effective_fields = None
        if effective_fields is not None and effective_fields and not isinstance(effective_fields[0], PrechatField):
            try:
                effective_fields = [PrechatField(**f) for f in effective_fields]
            except Exception as exc:
                raise ValueError("Invalid pre-chat field configuration") from exc
        _validate_prechat_settings(effective_enabled, effective_fields)

        # Handle nested objects
        if "prechat_fields" in update_data and update_data["prechat_fields"] is not None:
            update_data["prechat_fields"] = [
                f.model_dump() if isinstance(f, PrechatField) else f for f in update_data["prechat_fields"]
            ]

        if "business_hours" in update_data and update_data["business_hours"] is not None:
            bh = update_data["business_hours"]
            update_data["business_hours"] = bh.model_dump() if hasattr(bh, "model_dump") else bh

        for key, value in update_data.items():
            setattr(config, key, value)

        db.commit()
        db.refresh(config)
        logger.info("chat_widget_config_updated config_id=%s", config.id)
        return config

    @staticmethod
    def list(
        db: Session,
        is_active: bool | None = None,
        limit: int = 100,
    ) -> list[ChatWidgetConfig]:
        """List widget configurations."""
        query = db.query(ChatWidgetConfig)
        if is_active is not None:
            query = query.filter(ChatWidgetConfig.is_active == is_active)
        return query.order_by(ChatWidgetConfig.created_at.desc()).limit(limit).all()

    @staticmethod
    def delete(db: Session, config_id: UUID | str) -> bool:
        """Delete a widget configuration."""
        config = db.get(ChatWidgetConfig, coerce_uuid(config_id))
        if not config:
            return False
        db.delete(config)
        db.commit()
        logger.info("chat_widget_config_deleted config_id=%s", config_id)
        return True

    @staticmethod
    def validate_origin(config: ChatWidgetConfig, origin: str | None) -> bool:
        """Validate that the request origin is allowed."""
        if not config.allowed_domains:
            # No restrictions - allow all
            return True

        if not origin:
            # No origin header - reject
            return False

        domain = _extract_domain_from_origin(origin)
        if not domain:
            return False

        return any(_domain_matches_pattern(domain, pattern) for pattern in config.allowed_domains)

    @staticmethod
    def get_public_config(config: ChatWidgetConfig) -> ChatWidgetPublicConfig:
        """Get the public-facing configuration (safe to expose)."""
        prechat_fields = None
        if config.prechat_fields:
            prechat_fields = [PrechatField(**f) for f in config.prechat_fields]

        return ChatWidgetPublicConfig(
            widget_id=config.id,
            primary_color=config.primary_color,
            bubble_position=config.bubble_position,
            welcome_message=config.welcome_message,
            placeholder_text=config.placeholder_text,
            widget_title=config.widget_title,
            offline_message=config.offline_message,
            prechat_form_enabled=config.prechat_form_enabled,
            prechat_fields=prechat_fields,
            is_online=is_within_business_hours(config.business_hours),
        )

    @staticmethod
    def generate_embed_code(config: ChatWidgetConfig, base_url: str) -> str:
        """Generate the HTML embed code for the widget."""
        script_url = f"{base_url.rstrip('/')}/static/js/chat-widget.js"
        return f"""<!-- DotMac Chat Widget -->
<script>
  window.DotMacChatWidgetConfig = {{
    configId: '{config.id}',
    apiUrl: '{base_url.rstrip("/")}'
  }};
</script>
<script src="{script_url}" async></script>"""


class WidgetVisitorManager:
    """Manager for widget visitor sessions."""

    @staticmethod
    def create_session(
        db: Session,
        config_id: UUID | str,
        payload: WidgetSessionCreate,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[WidgetVisitorSession, str]:
        """Create a new visitor session. Returns (session, token)."""
        config = db.get(ChatWidgetConfig, coerce_uuid(config_id))
        if not config:
            raise ValueError("Widget configuration not found")

        fingerprint_hash = _hash_fingerprint(payload.fingerprint)

        # Check for existing session with same fingerprint
        if fingerprint_hash:
            existing = (
                db.query(WidgetVisitorSession)
                .filter(WidgetVisitorSession.widget_config_id == config.id)
                .filter(WidgetVisitorSession.fingerprint_hash == fingerprint_hash)
                .order_by(WidgetVisitorSession.created_at.desc())
                .first()
            )
            if existing:
                # Refresh activity and return existing session
                existing.last_active_at = _now()
                if payload.page_url:
                    existing.page_url = payload.page_url
                db.commit()
                db.refresh(existing)
                logger.info(
                    "widget_session_resumed session_id=%s fingerprint=%s",
                    existing.id,
                    fingerprint_hash[:8],
                )
                return existing, existing.visitor_token

        # Create new session
        token = _generate_visitor_token()
        session = WidgetVisitorSession(
            widget_config_id=config.id,
            visitor_token=token,
            fingerprint_hash=fingerprint_hash,
            ip_address=ip_address,
            user_agent=user_agent[:512] if user_agent else None,
            page_url=payload.page_url,
            referrer_url=payload.referrer_url,
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        logger.info("widget_session_created session_id=%s", session.id)
        return session, token

    @staticmethod
    def get_session_by_token(db: Session, token: str) -> WidgetVisitorSession | None:
        """Get a session by visitor token."""
        return db.query(WidgetVisitorSession).filter(WidgetVisitorSession.visitor_token == token).first()

    @staticmethod
    def get_session(db: Session, session_id: UUID | str) -> WidgetVisitorSession | None:
        """Get a session by ID."""
        return db.get(WidgetVisitorSession, coerce_uuid(session_id))

    @staticmethod
    def identify_visitor(
        db: Session,
        session: WidgetVisitorSession,
        email: str,
        name: str | None = None,
        phone: str | None = None,
        custom_fields: dict | None = None,
    ) -> WidgetVisitorSession:
        """Identify an anonymous visitor with contact info."""
        email_normalized = email.strip().lower()

        # Find or create person
        person = db.query(Person).filter(func.lower(Person.email) == email_normalized).first()

        if not person:
            # Create new person
            first_name = name.split()[0] if name else email_normalized.split("@")[0]
            last_name = " ".join(name.split()[1:]) if name and len(name.split()) > 1 else ""
            person = Person(
                email=email_normalized,
                first_name=first_name,
                last_name=last_name or first_name,
                display_name=name,
                phone=phone,
                party_status=PartyStatus.lead,
            )
            db.add(person)
            db.flush()
            logger.info("widget_person_created person_id=%s email=%s", person.id, email_normalized)
            existing_lead = db.query(Lead).filter(Lead.person_id == person.id).first()
            if not existing_lead:
                from app.schemas.crm.sales import LeadCreate
                from app.services.crm import leads as leads_service

                leads_service.create(
                    db=db,
                    payload=LeadCreate(person_id=person.id, lead_source="Website"),
                )
                # Keep new widget visitors as leads; leads_service upgrades to contact by default.
                person.party_status = PartyStatus.lead
                db.commit()
        else:
            if phone and not person.phone:
                person.phone = phone

        # Ensure person has chat_widget channel
        existing_channel = (
            db.query(PersonChannel)
            .filter(PersonChannel.person_id == person.id)
            .filter(PersonChannel.channel_type == PersonChannelType.chat_widget)
            .first()
        )
        if not existing_channel:
            channel = PersonChannel(
                person_id=person.id,
                channel_type=PersonChannelType.chat_widget,
                address=str(session.id),
                is_primary=False,
            )
            db.add(channel)
            db.flush()
        if phone:
            phone_channel = (
                db.query(PersonChannel)
                .filter(PersonChannel.person_id == person.id)
                .filter(PersonChannel.channel_type == PersonChannelType.phone)
                .filter(PersonChannel.address == phone)
                .first()
            )
            if not phone_channel:
                db.add(
                    PersonChannel(
                        person_id=person.id,
                        channel_type=PersonChannelType.phone,
                        address=phone,
                        is_primary=False,
                    )
                )
                db.flush()

        # Update session
        session.person_id = person.id
        session.is_identified = True
        session.identified_at = _now()
        session.identified_email = email_normalized
        session.identified_name = name
        if custom_fields:
            metadata = session.metadata_ or {}
            metadata.update(custom_fields)
            session.metadata_ = metadata

        db.commit()
        db.refresh(session)
        logger.info(
            "widget_visitor_identified session_id=%s person_id=%s email=%s",
            session.id,
            person.id,
            email_normalized,
        )
        return session

    @staticmethod
    def refresh_activity(db: Session, session: WidgetVisitorSession) -> None:
        """Update the last_active_at timestamp."""
        session.last_active_at = _now()
        db.commit()

    @staticmethod
    def check_rate_limit(
        db: Session,
        session: WidgetVisitorSession,
        config: ChatWidgetConfig,
    ) -> bool:
        """Check if the session is within rate limits. Returns True if allowed."""
        # Count messages in the last minute
        one_minute_ago = _now() - timedelta(minutes=1)

        if not session.conversation_id:
            return True

        message_count = (
            db.query(func.count(Message.id))
            .filter(Message.conversation_id == session.conversation_id)
            .filter(Message.direction == MessageDirection.inbound)
            .filter(Message.created_at >= one_minute_ago)
            .scalar()
        ) or 0

        return message_count < config.rate_limit_messages_per_minute


def receive_widget_message(
    db: Session,
    session: WidgetVisitorSession,
    body: str,
    metadata: dict | None = None,
    trace_id: str | None = None,
) -> Message:
    """
    Handle incoming widget message.

    Creates/updates conversation and message, broadcasts via WebSocket.
    """
    from app.schemas.crm.conversation import ConversationCreate, MessageCreate
    from app.services.crm import conversation as conversation_service

    config = session.widget_config

    # Sanitize message body
    sanitized_body = _sanitize_message_body(body)

    # Get or create person for the session
    person = None
    person_channel = None

    if session.person_id:
        person = db.get(Person, session.person_id)

    if not person:
        # Create anonymous person
        anonymous_email = f"widget-{session.id}@widget.local"
        person = Person(
            email=anonymous_email,
            first_name="Widget",
            last_name="Visitor",
            display_name=f"Widget Visitor {str(session.id)[:8]}",
        )
        db.add(person)
        db.flush()
        session.person_id = person.id
        logger.info("widget_anonymous_person_created person_id=%s session_id=%s", person.id, session.id)

    # Ensure person channel exists
    person_channel = (
        db.query(PersonChannel)
        .filter(PersonChannel.person_id == person.id)
        .filter(PersonChannel.channel_type == PersonChannelType.chat_widget)
        .first()
    )
    if not person_channel:
        person_channel = PersonChannel(
            person_id=person.id,
            channel_type=PersonChannelType.chat_widget,
            address=str(session.id),
            is_primary=False,
        )
        db.add(person_channel)
        db.flush()

    # Get or create conversation
    conversation = None
    is_new_conversation = False
    if session.conversation_id:
        conversation = db.get(Conversation, session.conversation_id)

    if not conversation:
        conversation = conversation_service.Conversations.create(
            db,
            ConversationCreate(
                person_id=person.id,
                subject=f"Chat from {config.name}",
                is_active=True,
            ),
        )
        session.conversation_id = conversation.id
        is_new_conversation = True
        db.commit()
        logger.info(
            "widget_conversation_created trace_id=%s conversation_id=%s session_id=%s",
            trace_id,
            conversation.id,
            session.id,
        )
    elif not conversation.is_active:
        conversation.is_active = True
        conversation.status = ConversationStatus.open
        db.commit()

    # Create message
    message_metadata = metadata or {}
    message_metadata["widget_config_id"] = str(config.id)
    message_metadata["session_id"] = str(session.id)
    if session.page_url:
        message_metadata["page_url"] = session.page_url

    message = conversation_service.Messages.create(
        db,
        MessageCreate(
            conversation_id=conversation.id,
            person_channel_id=person_channel.id,
            channel_type=ChannelType.chat_widget,
            direction=MessageDirection.inbound,
            status=MessageStatus.received,
            body=sanitized_body,
            received_at=_now(),
            metadata_=message_metadata,
        ),
    )

    # Broadcast to WebSocket subscribers
    from app.services.crm.inbox.routing import apply_routing_rules
    from app.websocket.broadcaster import (
        broadcast_conversation_summary,
        broadcast_new_message,
        subscribe_widget_to_conversation,
    )

    # Auto-subscribe widget visitor to their new conversation
    if is_new_conversation:
        subscribe_widget_to_conversation(str(session.id), str(conversation.id))

    apply_routing_rules(db, conversation=conversation, message=message)
    broadcast_new_message(message, conversation)
    from app.services.crm.inbox.notifications import notify_assigned_agent_new_reply

    notify_assigned_agent_new_reply(db, conversation, message)

    # Build conversation summary
    summary = {
        "preview": sanitized_body[:100] + "..." if len(sanitized_body) > 100 else sanitized_body,
        "last_message_at": message.received_at.isoformat() if message.received_at else None,
        "channel": ChannelType.chat_widget.value,
        "unread_count": 1,
    }
    broadcast_conversation_summary(str(conversation.id), summary)

    logger.info(
        "webchat_message_persisted trace_id=%s message_id=%s conversation_id=%s session_id=%s",
        trace_id,
        message.id,
        conversation.id,
        session.id,
    )

    return message


def send_widget_message(
    db: Session,
    session: WidgetVisitorSession,
    body: str,
    author_id: UUID | str | None = None,
    trace_id: str | None = None,
) -> Message:
    """
    Send a message from agent to widget visitor.

    This is called when an agent replies to a widget conversation.
    """
    from app.schemas.crm.conversation import MessageCreate
    from app.services.crm import conversation as conversation_service

    if not session.conversation_id:
        raise ValueError("Session has no conversation")

    conversation = db.get(Conversation, session.conversation_id)
    if not conversation:
        raise ValueError("Conversation not found")

    # Get person channel
    person_channel = None
    if session.person_id:
        person_channel = (
            db.query(PersonChannel)
            .filter(PersonChannel.person_id == session.person_id)
            .filter(PersonChannel.channel_type == PersonChannelType.chat_widget)
            .first()
        )

    # Create outbound message
    message = conversation_service.Messages.create(
        db,
        MessageCreate(
            conversation_id=conversation.id,
            person_channel_id=person_channel.id if person_channel else None,
            channel_type=ChannelType.chat_widget,
            direction=MessageDirection.outbound,
            status=MessageStatus.sent,
            body=body,
            author_id=coerce_uuid(author_id) if author_id else None,
            sent_at=_now(),
            metadata_={
                "widget_config_id": str(session.widget_config_id),
                "session_id": str(session.id),
            },
        ),
    )

    # Broadcast to admin subscribers
    from app.websocket.broadcaster import broadcast_new_message

    broadcast_new_message(message, conversation)

    logger.info(
        "webchat_message_sent trace_id=%s message_id=%s conversation_id=%s session_id=%s",
        trace_id,
        message.id,
        conversation.id,
        session.id,
    )

    return message


# Singleton instances
widget_configs = ChatWidgetConfigManager()
widget_visitors = WidgetVisitorManager()


# Backwards-compatible class aliases
class ChatWidgetConfigs(ChatWidgetConfigManager):
    pass


class WidgetVisitorSessions(WidgetVisitorManager):
    pass


# Backwards-compatible singleton aliases
chat_widget_configs = ChatWidgetConfigs()
widget_visitor_sessions = WidgetVisitorSessions()
