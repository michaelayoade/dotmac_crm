"""Public API endpoints for chat widget (no authentication required)."""

from __future__ import annotations

from uuid import UUID
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
import re
from sqlalchemy.orm import Session

from app.db import get_db
from app.logging import get_logger
from app.models.crm.chat_widget import ChatWidgetConfig, WidgetVisitorSession
from app.models.crm.conversation import Message
from app.models.crm.enums import MessageDirection
from app.schemas.crm.chat_widget import (
    ChatWidgetPublicConfig,
    PrechatField,
    WidgetIdentifyRequest,
    WidgetIdentifyResponse,
    WidgetMessageRead,
    WidgetMessageResponse,
    WidgetMessageSend,
    WidgetMessagesResponse,
    WidgetPrechatSubmit,
    WidgetSessionCreate,
    WidgetSessionRead,
)
from app.services.crm.chat_widget import (
    is_within_business_hours,
    receive_widget_message,
    widget_configs,
    widget_visitors,
)
from app.middleware.widget_rate_limit import check_session_creation_rate

logger = get_logger(__name__)

router = APIRouter(prefix="/widget", tags=["widget-public"])


def _get_client_ip(request: Request) -> str | None:
    """Extract client IP from request headers."""
    # Check common proxy headers
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        # Take the first IP in the chain
        return forwarded_for.split(",")[0].strip()

    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()

    # Fall back to direct connection
    if request.client:
        return request.client.host

    return None


def _validate_origin(
    config: ChatWidgetConfig,
    request: Request,
) -> None:
    """Validate Origin header against widget allowed domains."""
    origin = request.headers.get("origin")
    if not widget_configs.validate_origin(config, origin):
        logger.warning(
            "widget_origin_rejected config_id=%s origin=%s",
            config.id,
            origin,
        )
        raise HTTPException(status_code=403, detail="Origin not allowed")


def _set_cors_headers(response: Response, origin: str | None) -> None:
    if not origin:
        return
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"


def _normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits


def _validate_prechat_payload(config: ChatWidgetConfig, fields: dict) -> dict:
    if not config.prechat_form_enabled:
        raise HTTPException(status_code=400, detail="Pre-chat form is disabled")
    if not config.prechat_fields:
        raise HTTPException(status_code=400, detail="Pre-chat fields not configured")

    prechat_fields = [PrechatField(**f) for f in config.prechat_fields]
    field_map = {f.name: f for f in prechat_fields}
    errors: list[str] = []
    cleaned: dict[str, str] = {}
    email_value: str | None = None
    name_value: str | None = None
    phone_value: str | None = None

    for name in fields.keys():
        if name not in field_map:
            errors.append(f"Unknown field: {name}")

    for field in prechat_fields:
        raw = fields.get(field.name)
        value = raw.strip() if isinstance(raw, str) else ""

        if field.required and not value:
            errors.append(f"{field.label} is required")
            continue

        if not value:
            continue

        if field.field_type == "email":
            if not re.match(r"^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", value):
                errors.append(f"{field.label} must be a valid email")
            else:
                email_value = value
                cleaned[field.name] = value
        elif field.field_type == "phone":
            digits = _normalize_phone(value)
            if len(digits) < 7:
                errors.append(f"{field.label} must be a valid phone number")
            else:
                phone_value = digits
                cleaned[field.name] = digits
        elif field.field_type == "select":
            if not field.options or value not in field.options:
                errors.append(f"{field.label} must be one of the allowed options")
            else:
                cleaned[field.name] = value
        else:
            cleaned[field.name] = value

        if field.name in ("name", "full_name") and value:
            name_value = value

    if not email_value:
        errors.append("Email is required")

    if errors:
        raise HTTPException(status_code=400, detail=", ".join(errors))

    return {
        "email": email_value,
        "name": name_value,
        "phone": phone_value,
        "custom_fields": {k: v for k, v in cleaned.items() if k not in ("email", "name", "full_name")},
    }


@router.options("/{path:path}")
def widget_options(request: Request, path: str):
    origin = request.headers.get("origin")
    response = Response(status_code=204)
    _set_cors_headers(response, origin)
    return response


def _get_session_from_token(
    db: Session,
    visitor_token: str | None,
) -> WidgetVisitorSession:
    """Get session from token or raise 401."""
    if not visitor_token:
        raise HTTPException(status_code=401, detail="Visitor token required")

    session = widget_visitors.get_session_by_token(db, visitor_token)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid visitor token")

    return session


@router.get("/{config_id}/config", response_model=ChatWidgetPublicConfig)
def get_widget_config(
    config_id: UUID,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """
    Get public widget configuration.

    Validates Origin header against allowed domains.
    Returns only public-safe configuration data.
    """
    config = widget_configs.get(db, config_id)
    if not config or not config.is_active:
        raise HTTPException(status_code=404, detail="Widget not found")

    _validate_origin(config, request)
    _set_cors_headers(response, request.headers.get("origin"))

    return widget_configs.get_public_config(config)


@router.post("/{config_id}/session", response_model=WidgetSessionRead)
def create_session(
    config_id: UUID,
    payload: WidgetSessionCreate,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """
    Create a new visitor session.

    Returns a session with visitor_token for subsequent requests.
    If fingerprint matches an existing session, returns that session instead.
    """
    config = widget_configs.get(db, config_id)
    if not config or not config.is_active:
        raise HTTPException(status_code=404, detail="Widget not found")

    _validate_origin(config, request)
    _set_cors_headers(response, request.headers.get("origin"))

    ip_address = _get_client_ip(request)

    # Check IP-based rate limit for session creation
    allowed, remaining = check_session_creation_rate(
        ip_address or "unknown",
        limit=config.rate_limit_sessions_per_ip,
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many session requests. Please try again later.",
            headers={"Retry-After": "300"},
        )
    user_agent = request.headers.get("user-agent")

    try:
        session, token = widget_visitors.create_session(
            db,
            config_id,
            payload,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return WidgetSessionRead(
        session_id=session.id,
        visitor_token=token,
        conversation_id=session.conversation_id,
        is_identified=session.is_identified,
        identified_name=session.identified_name,
    )


@router.post("/session/{session_id}/identify", response_model=WidgetIdentifyResponse)
def identify_visitor(
    session_id: UUID,
    payload: WidgetIdentifyRequest,
    request: Request,
    response: Response,
    x_visitor_token: str = Header(alias="X-Visitor-Token"),
    db: Session = Depends(get_db),
):
    """
    Identify an anonymous visitor with email/name.

    Creates or links to an existing Person record.
    """
    session = _get_session_from_token(db, x_visitor_token)

    if str(session.id) != str(session_id):
        raise HTTPException(status_code=403, detail="Session mismatch")

    config = session.widget_config
    if config:
        _validate_origin(config, request)
        _set_cors_headers(response, request.headers.get("origin"))

    try:
        session = widget_visitors.identify_visitor(
            db,
            session,
            email=payload.email,
            name=payload.name,
            custom_fields=payload.custom_fields,
        )
    except Exception as e:
        logger.error("widget_identify_error session_id=%s error=%s", session_id, e)
        raise HTTPException(status_code=400, detail="Identification failed")

    if not session.person_id or not session.identified_email:
        raise HTTPException(status_code=400, detail="Visitor not identified")
    return WidgetIdentifyResponse(
        session_id=session.id,
        person_id=session.person_id,
        email=session.identified_email,
        name=session.identified_name,
    )


@router.post("/session/{session_id}/prechat", response_model=WidgetIdentifyResponse)
def submit_prechat(
    session_id: UUID,
    payload: WidgetPrechatSubmit,
    request: Request,
    response: Response,
    x_visitor_token: str = Header(alias="X-Visitor-Token"),
    db: Session = Depends(get_db),
):
    """Submit pre-chat form and create a lead."""
    session = _get_session_from_token(db, x_visitor_token)

    if str(session.id) != str(session_id):
        raise HTTPException(status_code=403, detail="Session mismatch")

    config = session.widget_config
    if not config:
        raise HTTPException(status_code=400, detail="Widget configuration not found")

    _validate_origin(config, request)
    _set_cors_headers(response, request.headers.get("origin"))

    values = _validate_prechat_payload(config, payload.fields or {})

    try:
        session = widget_visitors.identify_visitor(
            db,
            session,
            email=values["email"],
            name=values.get("name"),
            phone=values.get("phone"),
            custom_fields=values.get("custom_fields"),
        )
    except Exception as e:
        logger.error("widget_prechat_error session_id=%s error=%s", session_id, e)
        raise HTTPException(status_code=400, detail="Pre-chat submission failed")

    if not session.person_id or not session.identified_email:
        raise HTTPException(status_code=400, detail="Visitor not identified")
    return WidgetIdentifyResponse(
        session_id=session.id,
        person_id=session.person_id,
        email=session.identified_email,
        name=session.identified_name,
    )


@router.post("/session/{session_id}/message", response_model=WidgetMessageResponse)
def send_message(
    session_id: UUID,
    payload: WidgetMessageSend,
    request: Request,
    response: Response,
    x_visitor_token: str = Header(alias="X-Visitor-Token"),
    db: Session = Depends(get_db),
):
    """
    Send a message from the widget visitor.

    Creates conversation if needed, creates message, broadcasts via WebSocket.
    """
    session = _get_session_from_token(db, x_visitor_token)

    if str(session.id) != str(session_id):
        raise HTTPException(status_code=403, detail="Session mismatch")

    config = session.widget_config
    if not config:
        raise HTTPException(status_code=400, detail="Widget configuration not found")

    _validate_origin(config, request)
    _set_cors_headers(response, request.headers.get("origin"))

    # Check rate limit
    if not widget_visitors.check_rate_limit(db, session, config):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    # Refresh activity
    widget_visitors.refresh_activity(db, session)

    try:
        message = receive_widget_message(
            db,
            session,
            body=payload.body,
        )
    except Exception as e:
        logger.error("widget_message_error session_id=%s error=%s", session_id, e)
        raise HTTPException(status_code=500, detail="Failed to send message")

    return WidgetMessageResponse(
        message_id=message.id,
        conversation_id=message.conversation_id,
        status=message.status.value if message.status else "received",
        body=message.body or "",
        created_at=message.created_at,
    )


@router.get("/session/{session_id}/messages", response_model=WidgetMessagesResponse)
def get_messages(
    session_id: UUID,
    request: Request,
    response: Response,
    x_visitor_token: str = Header(alias="X-Visitor-Token"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """
    Get conversation history for the visitor.

    Returns messages in chronological order (oldest first).
    """
    session = _get_session_from_token(db, x_visitor_token)

    if str(session.id) != str(session_id):
        raise HTTPException(status_code=403, detail="Session mismatch")

    config = session.widget_config
    if config:
        _validate_origin(config, request)
        _set_cors_headers(response, request.headers.get("origin"))

    # Refresh activity
    widget_visitors.refresh_activity(db, session)

    if not session.conversation_id:
        return WidgetMessagesResponse(messages=[], has_more=False)

    # Query messages
    query = (
        db.query(Message)
        .filter(Message.conversation_id == session.conversation_id)
        .order_by(Message.created_at.asc())
    )

    messages = query.offset(offset).limit(limit + 1).all()

    has_more = len(messages) > limit
    if has_more:
        messages = messages[:limit]

    result = []
    for msg in messages:
        direction: Literal["inbound", "outbound"] = (
            "inbound" if msg.direction == MessageDirection.inbound else "outbound"
        )

        # Get author info for outbound messages
        author_name = None
        author_avatar = None
        if msg.direction == MessageDirection.outbound and msg.author:
            author_name = msg.author.display_name or f"{msg.author.first_name} {msg.author.last_name}"
            author_avatar = msg.author.avatar_url

        result.append(
            WidgetMessageRead(
                id=msg.id,
                body=msg.body or "",
                direction=direction,
                created_at=msg.created_at,
                author_name=author_name,
                author_avatar=author_avatar,
            )
        )

    return WidgetMessagesResponse(messages=result, has_more=has_more)


@router.get("/session/{session_id}/status")
def get_session_status(
    session_id: UUID,
    request: Request,
    response: Response,
    x_visitor_token: str = Header(alias="X-Visitor-Token"),
    db: Session = Depends(get_db),
):
    """
    Get current session status.

    Used for polling when WebSocket is not available.
    """
    session = _get_session_from_token(db, x_visitor_token)

    if str(session.id) != str(session_id):
        raise HTTPException(status_code=403, detail="Session mismatch")

    config = session.widget_config
    if config:
        _validate_origin(config, request)
        _set_cors_headers(response, request.headers.get("origin"))

    # Get unread count
    unread_count = 0
    if session.conversation_id:
        unread_count = (
            db.query(Message)
            .filter(Message.conversation_id == session.conversation_id)
            .filter(Message.direction == MessageDirection.outbound)
            .filter(Message.read_at.is_(None))
            .count()
        )

    return {
        "session_id": str(session.id),
        "conversation_id": str(session.conversation_id) if session.conversation_id else None,
        "is_identified": session.is_identified,
        "unread_count": unread_count,
        "is_online": is_within_business_hours(config.business_hours) if config else True,
    }
