"""Service helpers for CRM chat widget web routes."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.crm.chat_widget import ChatWidgetConfig, WidgetVisitorSession
from app.schemas.crm.chat_widget import ChatWidgetConfigCreate, ChatWidgetConfigUpdate


def _form_str(form, key: str, default: str = "") -> str:
    value = form.get(key)
    return value.strip() if isinstance(value, str) else default


def _form_str_opt(form, key: str) -> str | None:
    value = form.get(key)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _as_int(value: str | None, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value.strip()) if isinstance(value, str) else int(value)
    except ValueError:
        return default


def _coerce_bubble_position(value: str | None) -> str:
    return "bottom-left" if value == "bottom-left" else "bottom-right"


def parse_prechat_fields(form) -> Any:
    raw = _form_str(form, "prechat_fields_json")
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except Exception as exc:
        raise ValueError("Invalid pre-chat field configuration") from exc


def parse_allowed_domains(form) -> list[str]:
    allowed_domains_str = _form_str(form, "allowed_domains")
    return [d.strip() for d in allowed_domains_str.split(",") if d.strip()] if allowed_domains_str else []


def widget_create_payload_from_form(form) -> ChatWidgetConfigCreate:
    prechat_fields = parse_prechat_fields(form)
    allowed_domains = parse_allowed_domains(form)
    return ChatWidgetConfigCreate(
        name=_form_str(form, "name"),
        allowed_domains=allowed_domains,
        primary_color=_form_str(form, "primary_color", "#3B82F6"),
        bubble_position=_coerce_bubble_position(_form_str_opt(form, "bubble_position")),
        widget_title=_form_str(form, "widget_title", "Chat with us"),
        welcome_message=_form_str_opt(form, "welcome_message"),
        placeholder_text=_form_str(form, "placeholder_text", "Type a message..."),
        rate_limit_messages_per_minute=_as_int(_form_str_opt(form, "rate_limit_messages_per_minute"), 10) or 10,
        rate_limit_sessions_per_ip=_as_int(_form_str_opt(form, "rate_limit_sessions_per_ip"), 5) or 5,
        prechat_form_enabled="prechat_form_enabled" in form,
        prechat_fields=prechat_fields,
    )


def widget_update_payload_from_form(form) -> ChatWidgetConfigUpdate:
    prechat_fields = parse_prechat_fields(form)
    allowed_domains = parse_allowed_domains(form)
    bubble_position_value = _form_str_opt(form, "bubble_position")
    return ChatWidgetConfigUpdate(
        name=_form_str_opt(form, "name"),
        allowed_domains=allowed_domains,
        primary_color=_form_str_opt(form, "primary_color"),
        bubble_position=_coerce_bubble_position(bubble_position_value) if bubble_position_value else None,
        widget_title=_form_str_opt(form, "widget_title"),
        welcome_message=_form_str_opt(form, "welcome_message"),
        placeholder_text=_form_str_opt(form, "placeholder_text"),
        rate_limit_messages_per_minute=_as_int(_form_str_opt(form, "rate_limit_messages_per_minute"), 10) or 10,
        rate_limit_sessions_per_ip=_as_int(_form_str_opt(form, "rate_limit_sessions_per_ip"), 5) or 5,
        is_active="is_active" in form,
        prechat_form_enabled="prechat_form_enabled" in form,
        prechat_fields=prechat_fields,
    )


def widget_list_data(db: Session) -> dict[str, Any]:
    widgets = db.query(ChatWidgetConfig).order_by(ChatWidgetConfig.created_at.desc()).all()
    return {"widgets": widgets}


def widget_detail_data(db: Session, *, widget, base_url: str) -> dict[str, Any] | None:
    if not widget:
        return None

    from app.services.crm.chat_widget import widget_configs

    embed_code = widget_configs.generate_embed_code(widget, base_url)
    session_count = db.query(WidgetVisitorSession).filter(WidgetVisitorSession.widget_config_id == widget.id).count()
    conversation_count = (
        db.query(WidgetVisitorSession)
        .filter(WidgetVisitorSession.widget_config_id == widget.id)
        .filter(WidgetVisitorSession.conversation_id.isnot(None))
        .count()
    )
    return {
        "widget": widget,
        "embed_code": embed_code,
        "session_count": session_count,
        "conversation_count": conversation_count,
    }


__all__ = [
    "widget_create_payload_from_form",
    "widget_detail_data",
    "widget_list_data",
    "widget_update_payload_from_form",
]
