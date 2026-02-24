"""Context builder for CRM inbox settings UI."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.connector import ConnectorType
from app.models.domain_settings import SettingDomain
from app.models.person import Person
from app.services import crm as crm_service
from app.services import person as person_service
from app.services.crm.chat_widget import widget_configs
from app.services.crm.inbox.csat import get_enabled_map as get_csat_enabled_map
from app.services.crm.inbox.inboxes import (
    get_email_channel_state,
    get_whatsapp_channel_state,
    list_channel_targets,
)
from app.services.crm.inbox.macros import conversation_macros
from app.services.crm.inbox.meta_status import get_meta_connection_status
from app.services.crm.inbox.permissions import (
    can_manage_inbox_settings,
    can_view_inbox_settings,
)
from app.services.crm.inbox.templates import message_templates
from app.services.settings_spec import resolve_value


def build_inbox_settings_context(
    db: Session,
    *,
    query_params: Mapping[str, str],
    headers: Mapping[str, str],
    current_user: dict | None,
    sidebar_stats: dict,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> dict[str, Any]:
    if not can_view_inbox_settings(roles, scopes):
        raise HTTPException(status_code=403, detail="Forbidden")
    can_manage = can_manage_inbox_settings(roles, scopes)
    email_channel = get_email_channel_state(db)
    whatsapp_channel = get_whatsapp_channel_state(db)
    email_inboxes = list_channel_targets(db, ConnectorType.email)
    whatsapp_inboxes = list_channel_targets(db, ConnectorType.whatsapp)
    facebook_inboxes = list_channel_targets(db, ConnectorType.facebook)
    instagram_inboxes = list_channel_targets(db, ConnectorType.instagram)

    email_setup = query_params.get("email_setup")
    email_error = query_params.get("email_error")
    email_error_detail = query_params.get("email_error_detail")
    email_warning = query_params.get("email_warning")
    email_warning_detail = query_params.get("email_warning_detail")
    whatsapp_setup = query_params.get("whatsapp_setup")
    whatsapp_error = query_params.get("whatsapp_error")
    team_setup = query_params.get("team_setup")
    team_error = query_params.get("team_error")
    team_error_detail = query_params.get("team_error_detail")
    agent_setup = query_params.get("agent_setup")
    agent_deleted = query_params.get("agent_deleted")
    agent_error = query_params.get("agent_error")
    agent_error_detail = query_params.get("agent_error_detail")
    assignment_setup = query_params.get("assignment_setup")
    assignment_error = query_params.get("assignment_error")
    assignment_error_detail = query_params.get("assignment_error_detail")
    notification_setup = query_params.get("notification_setup")
    notification_error = query_params.get("notification_error")
    notification_error_detail = query_params.get("notification_error_detail")
    csat_setup = query_params.get("csat_setup")
    csat_error = query_params.get("csat_error")
    csat_error_detail = query_params.get("csat_error_detail")

    macro_setup = query_params.get("macro_setup")
    macro_error = query_params.get("macro_error")
    macro_error_detail = query_params.get("macro_error_detail")

    meta_setup = query_params.get("meta_setup")
    meta_error = query_params.get("meta_error")
    meta_error_detail = query_params.get("meta_error_detail")
    meta_disconnected = query_params.get("meta_disconnected")
    meta_pages = query_params.get("pages")
    meta_instagram = query_params.get("instagram")

    meta_status = get_meta_connection_status(db)

    reminder_delay_seconds = resolve_value(db, SettingDomain.notification, "crm_inbox_reply_reminder_delay_seconds")
    reminder_repeat_enabled = resolve_value(db, SettingDomain.notification, "crm_inbox_reply_reminder_repeat_enabled")
    reminder_repeat_interval_seconds = resolve_value(
        db,
        SettingDomain.notification,
        "crm_inbox_reply_reminder_repeat_interval_seconds",
    )
    notification_auto_dismiss_seconds = resolve_value(
        db, SettingDomain.notification, "crm_inbox_notification_auto_dismiss_seconds"
    )
    auto_resolve_enabled = resolve_value(db, SettingDomain.notification, "crm_inbox_auto_resolve_enabled")
    auto_resolve_days = resolve_value(db, SettingDomain.notification, "crm_inbox_auto_resolve_days")
    csat_enabled_by_target = get_csat_enabled_map(db)
    for inbox in email_inboxes:
        inbox["csat_enabled"] = bool(csat_enabled_by_target.get(str(inbox.get("target_id")), False))
    for inbox in whatsapp_inboxes:
        inbox["csat_enabled"] = bool(csat_enabled_by_target.get(str(inbox.get("target_id")), False))
    for inbox in facebook_inboxes:
        inbox["csat_enabled"] = bool(csat_enabled_by_target.get(str(inbox.get("target_id")), False))
    for inbox in instagram_inboxes:
        inbox["csat_enabled"] = bool(csat_enabled_by_target.get(str(inbox.get("target_id")), False))
    chat_widget_csat_enabled = bool(csat_enabled_by_target.get("channel:chat_widget", False))

    teams = crm_service.teams.list(
        db=db,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    agents = crm_service.agents.list(
        db=db,
        person_id=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    agent_teams = crm_service.agent_teams.list(
        db=db,
        agent_id=None,
        team_id=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=500,
        offset=0,
    )
    people = person_service.people.list(
        db=db,
        email=None,
        status=None,
        party_status=None,
        organization_id=None,
        is_active=True,
        order_by="last_name",
        order_dir="asc",
        limit=500,
        offset=0,
    )
    people_by_id = {str(person.id): person for person in people}
    for agent in agents:
        person_key = str(agent.person_id)
        if person_key not in people_by_id:
            person = db.get(Person, agent.person_id)
            if person:
                people_by_id[person_key] = person
    teams_by_id = {str(team.id): team for team in teams}
    widgets = widget_configs.list(db=db, is_active=None, limit=100)
    routing_rules = crm_service.routing_rules.list(
        db=db,
        team_id=None,
        channel_type=None,
        is_active=None,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    templates = message_templates.list(
        db,
        channel_type=None,
        is_active=None,
        limit=200,
        offset=0,
    )
    host = headers.get("host", "localhost:8000")
    scheme = headers.get("x-forwarded-proto", "http")
    base_url = f"{scheme}://{host}"
    for widget in widgets:
        cast(Any, widget).embed_code = widget_configs.generate_embed_code(widget, base_url)

    return {
        "current_user": current_user,
        "sidebar_stats": sidebar_stats,
        "active_page": "inbox",
        "can_manage_settings": can_manage,
        "email_channel": email_channel,
        "whatsapp_channel": whatsapp_channel,
        "email_inboxes": email_inboxes,
        "whatsapp_inboxes": whatsapp_inboxes,
        "facebook_inboxes": facebook_inboxes,
        "instagram_inboxes": instagram_inboxes,
        "chat_widget_csat_enabled": chat_widget_csat_enabled,
        "email_setup": email_setup,
        "email_error": email_error,
        "email_error_detail": email_error_detail,
        "email_warning": email_warning,
        "email_warning_detail": email_warning_detail,
        "whatsapp_setup": whatsapp_setup,
        "whatsapp_error": whatsapp_error,
        "team_setup": team_setup,
        "team_error": team_error,
        "team_error_detail": team_error_detail,
        "agent_setup": agent_setup,
        "agent_deleted": agent_deleted,
        "agent_error": agent_error,
        "agent_error_detail": agent_error_detail,
        "assignment_setup": assignment_setup,
        "assignment_error": assignment_error,
        "assignment_error_detail": assignment_error_detail,
        "notification_setup": notification_setup,
        "notification_error": notification_error,
        "notification_error_detail": notification_error_detail,
        "csat_setup": csat_setup,
        "csat_error": csat_error,
        "csat_error_detail": csat_error_detail,
        "meta_setup": meta_setup,
        "meta_error": meta_error,
        "meta_error_detail": meta_error_detail,
        "meta_disconnected": meta_disconnected,
        "meta_pages": meta_pages,
        "meta_instagram": meta_instagram,
        "meta_status": meta_status,
        "reminder_delay_seconds": reminder_delay_seconds,
        "reminder_repeat_enabled": reminder_repeat_enabled,
        "reminder_repeat_interval_seconds": reminder_repeat_interval_seconds,
        "notification_auto_dismiss_seconds": notification_auto_dismiss_seconds,
        "auto_resolve_enabled": auto_resolve_enabled,
        "auto_resolve_days": auto_resolve_days,
        "teams": teams,
        "agents": agents,
        "agent_teams": agent_teams,
        "people": people,
        "people_by_id": people_by_id,
        "teams_by_id": teams_by_id,
        "widgets": widgets,
        "routing_rules": routing_rules,
        "message_templates": templates,
        "macros": conversation_macros.list(db, is_active=None, limit=200),
        "macro_setup": macro_setup,
        "macro_error": macro_error,
        "macro_error_detail": macro_error_detail,
    }
