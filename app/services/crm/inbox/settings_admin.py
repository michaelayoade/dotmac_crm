"""Admin actions for CRM inbox settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.crm.conversation import ConversationAssignment
from app.models.crm.enums import ChannelType
from app.models.crm.presence import AgentPresence
from app.models.crm.sales import Lead
from app.models.crm.team import CrmAgent, CrmAgentTeam
from app.models.domain_settings import SettingDomain, SettingValueType
from app.schemas.crm.message_template import MessageTemplateCreate, MessageTemplateUpdate
from app.schemas.crm.team import AgentCreate, AgentTeamCreate, RoutingRuleCreate, RoutingRuleUpdate, TeamCreate
from app.schemas.settings import DomainSettingUpdate
from app.services import crm as crm_service
from app.services import domain_settings as domain_settings_service
from app.services import settings_spec
from app.services.common import coerce_uuid
from app.services.crm.inbox.permissions import can_manage_inbox_settings


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    error_detail: str | None = None


@dataclass(frozen=True)
class NotificationSettingsResult:
    ok: bool
    error_detail: str | None = None


@dataclass(frozen=True)
class BulkAgentUpdateResult:
    ok: bool
    error_detail: str | None = None


def _coerce_value_json(value: object | None) -> dict[Any, Any] | list[Any] | bool | int | str | None:
    if isinstance(value, dict | list | bool | int | str):
        return value
    return None


def _coerce_int(key: str, raw_value: str) -> int:
    spec = settings_spec.get_spec(SettingDomain.notification, key)
    if not spec:
        return 0
    try:
        parsed = int(str(raw_value).strip())
    except (TypeError, ValueError):
        parsed = spec.default if isinstance(spec.default, int) else 0
    if spec.min_value is not None and parsed < spec.min_value:
        parsed = spec.min_value
    if spec.max_value is not None and parsed > spec.max_value:
        parsed = spec.max_value
    return parsed


def update_notification_settings(
    db: Session,
    *,
    reminder_delay_seconds: str,
    reminder_repeat_enabled: str | None,
    reminder_repeat_interval_seconds: str,
    notification_auto_dismiss_seconds: str,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> NotificationSettingsResult:
    try:
        if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
            return NotificationSettingsResult(
                ok=False,
                error_detail="Not authorized to update notification settings",
            )
        reminder_delay = _coerce_int("crm_inbox_reply_reminder_delay_seconds", reminder_delay_seconds)
        repeat_enabled = bool(reminder_repeat_enabled)
        reminder_repeat_interval = _coerce_int(
            "crm_inbox_reply_reminder_repeat_interval_seconds",
            reminder_repeat_interval_seconds,
        )
        auto_dismiss_seconds = _coerce_int(
            "crm_inbox_notification_auto_dismiss_seconds",
            notification_auto_dismiss_seconds,
        )
        settings_service = domain_settings_service.DomainSettings(SettingDomain.notification)

        spec = settings_spec.get_spec(SettingDomain.notification, "crm_inbox_reply_reminder_delay_seconds")
        if spec:
            value_text, value_json = settings_spec.normalize_for_db(spec, reminder_delay)
            settings_service.upsert_by_key(
                db,
                "crm_inbox_reply_reminder_delay_seconds",
                DomainSettingUpdate(
                    value_type=SettingValueType.integer,
                    value_text=value_text,
                    value_json=_coerce_value_json(value_json),
                ),
            )

        spec = settings_spec.get_spec(SettingDomain.notification, "crm_inbox_reply_reminder_repeat_enabled")
        if spec:
            value_text, value_json = settings_spec.normalize_for_db(spec, repeat_enabled)
            settings_service.upsert_by_key(
                db,
                "crm_inbox_reply_reminder_repeat_enabled",
                DomainSettingUpdate(
                    value_type=SettingValueType.boolean,
                    value_text=value_text,
                    value_json=_coerce_value_json(value_json),
                ),
            )

        spec = settings_spec.get_spec(
            SettingDomain.notification,
            "crm_inbox_reply_reminder_repeat_interval_seconds",
        )
        if spec:
            value_text, value_json = settings_spec.normalize_for_db(spec, reminder_repeat_interval)
            settings_service.upsert_by_key(
                db,
                "crm_inbox_reply_reminder_repeat_interval_seconds",
                DomainSettingUpdate(
                    value_type=SettingValueType.integer,
                    value_text=value_text,
                    value_json=_coerce_value_json(value_json),
                ),
            )

        spec = settings_spec.get_spec(
            SettingDomain.notification,
            "crm_inbox_notification_auto_dismiss_seconds",
        )
        if spec:
            value_text, value_json = settings_spec.normalize_for_db(spec, auto_dismiss_seconds)
            settings_service.upsert_by_key(
                db,
                "crm_inbox_notification_auto_dismiss_seconds",
                DomainSettingUpdate(
                    value_type=SettingValueType.integer,
                    value_text=value_text,
                    value_json=_coerce_value_json(value_json),
                ),
            )
        return NotificationSettingsResult(ok=True)
    except Exception as exc:
        return NotificationSettingsResult(
            ok=False,
            error_detail=str(exc) or "Failed to save notification settings",
        )


def create_team(
    db: Session,
    *,
    name: str,
    notes: str | None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> ActionResult:
    try:
        if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
            return ActionResult(ok=False, error_detail="Not authorized to create teams")
        payload = TeamCreate(
            name=name.strip(),
            notes=notes.strip() if notes else None,
        )
        crm_service.teams.create(db, payload)
        return ActionResult(ok=True)
    except Exception as exc:
        return ActionResult(ok=False, error_detail=str(exc) or "Failed to create team")


def create_agent(
    db: Session,
    *,
    person_id: str | None,
    title: str | None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> ActionResult:
    try:
        if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
            return ActionResult(ok=False, error_detail="Not authorized to create agents")
        person_id_value = (person_id or "").strip()
        if not person_id_value:
            raise ValueError("Please select a person for the agent")
        existing = crm_service.agents.list(
            db=db,
            person_id=person_id_value,
            is_active=None,
            order_by="created_at",
            order_dir="desc",
            limit=1,
            offset=0,
        )
        if existing:
            raise ValueError("Agent already exists for that person")
        payload = AgentCreate(
            person_id=coerce_uuid(person_id_value),
            title=title.strip() if title else None,
        )
        crm_service.agents.create(db, payload)
        return ActionResult(ok=True)
    except Exception as exc:
        return ActionResult(ok=False, error_detail=str(exc) or "Failed to create agent")


def create_agent_team(
    db: Session,
    *,
    agent_id: str,
    team_id: str,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> ActionResult:
    try:
        if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
            return ActionResult(ok=False, error_detail="Not authorized to assign agent to team")
        payload = AgentTeamCreate(
            agent_id=coerce_uuid(agent_id),
            team_id=coerce_uuid(team_id),
        )
        crm_service.agent_teams.create(db, payload)
        return ActionResult(ok=True)
    except Exception as exc:
        return ActionResult(
            ok=False,
            error_detail=str(exc) or "Failed to assign agent to team",
        )


def deactivate_agent(
    db: Session,
    *,
    agent_id: str,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> ActionResult:
    try:
        if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
            return ActionResult(ok=False, error_detail="Not authorized to update agents")
        agent = db.get(CrmAgent, coerce_uuid(agent_id))
        if not agent:
            return ActionResult(ok=False, error_detail="Agent not found")
        agent.is_active = False
        db.query(CrmAgentTeam).filter(
            CrmAgentTeam.agent_id == agent.id,
            CrmAgentTeam.is_active.is_(True),
        ).update({"is_active": False})
        db.commit()
        return ActionResult(ok=True)
    except Exception as exc:
        return ActionResult(ok=False, error_detail=str(exc) or "Failed to update agent")


def bulk_update_agents(
    db: Session,
    *,
    action: str,
    agent_ids: list[str],
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> BulkAgentUpdateResult:
    selected_ids = [agent_id.strip() for agent_id in agent_ids if agent_id and agent_id.strip()]
    if not selected_ids:
        return BulkAgentUpdateResult(ok=False, error_detail="No agents selected")

    normalized_action = action.strip().lower()
    if normalized_action != "deactivate":
        return BulkAgentUpdateResult(ok=False, error_detail="Unsupported bulk action")

    failures = 0
    for agent_id in selected_ids:
        result = deactivate_agent(
            db,
            agent_id=agent_id,
            roles=roles,
            scopes=scopes,
        )
        if not result.ok:
            failures += 1
    if failures:
        return BulkAgentUpdateResult(
            ok=False,
            error_detail=f"Failed to update {failures} selected agent(s)",
        )
    return BulkAgentUpdateResult(ok=True)


def activate_agent(
    db: Session,
    *,
    agent_id: str,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> ActionResult:
    try:
        if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
            return ActionResult(ok=False, error_detail="Not authorized to update agents")
        agent = db.get(CrmAgent, coerce_uuid(agent_id))
        if not agent:
            return ActionResult(ok=False, error_detail="Agent not found")
        agent.is_active = True
        db.commit()
        return ActionResult(ok=True)
    except Exception as exc:
        return ActionResult(ok=False, error_detail=str(exc) or "Failed to update agent")


def hard_delete_agent(
    db: Session,
    *,
    agent_id: str,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> ActionResult:
    try:
        if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
            return ActionResult(ok=False, error_detail="Not authorized to delete agents")
        agent = db.get(CrmAgent, coerce_uuid(agent_id))
        if not agent:
            return ActionResult(ok=False, error_detail="Agent not found")

        # Remove hard-FK children first, then null optional references, then delete agent.
        db.query(AgentPresence).filter(AgentPresence.agent_id == agent.id).delete(synchronize_session=False)
        db.query(CrmAgentTeam).filter(CrmAgentTeam.agent_id == agent.id).delete(synchronize_session=False)
        db.query(ConversationAssignment).filter(ConversationAssignment.agent_id == agent.id).update(
            {"agent_id": None}, synchronize_session=False
        )
        db.query(Lead).filter(Lead.owner_agent_id == agent.id).update(
            {"owner_agent_id": None},
            synchronize_session=False,
        )
        db.delete(agent)
        db.commit()
        return ActionResult(ok=True)
    except Exception as exc:
        db.rollback()
        return ActionResult(ok=False, error_detail=str(exc) or "Failed to delete agent")


def remove_agent_team(
    db: Session,
    *,
    link_id: str,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> ActionResult:
    try:
        if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
            return ActionResult(ok=False, error_detail="Not authorized to update agent teams")
        link = db.get(CrmAgentTeam, coerce_uuid(link_id))
        if not link:
            return ActionResult(ok=False, error_detail="Agent team link not found")
        link.is_active = False
        db.commit()
        return ActionResult(ok=True)
    except Exception as exc:
        return ActionResult(ok=False, error_detail=str(exc) or "Failed to update agent team")


def create_message_template(
    db: Session,
    *,
    name: str,
    channel_type: str,
    subject: str | None,
    body: str,
    is_active: str | None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> ActionResult:
    from app.services.crm.inbox.templates import message_templates

    try:
        if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
            return ActionResult(ok=False, error_detail="Forbidden")
        try:
            channel_enum = ChannelType(channel_type)
        except ValueError as exc:
            raise ValueError("Invalid channel type") from exc
        payload = MessageTemplateCreate(
            name=name.strip(),
            channel_type=channel_enum,
            subject=subject.strip() if subject else None,
            body=body.strip(),
            is_active=bool(is_active),
        )
        message_templates.create(db, payload)
        return ActionResult(ok=True)
    except Exception as exc:
        return ActionResult(ok=False, error_detail=str(exc) or "Failed to create template")


def update_message_template(
    db: Session,
    *,
    template_id: str,
    name: str,
    channel_type: str,
    subject: str | None,
    body: str,
    is_active: str | None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> ActionResult:
    from app.services.crm.inbox.templates import message_templates

    try:
        if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
            return ActionResult(ok=False, error_detail="Forbidden")
        try:
            channel_enum = ChannelType(channel_type)
        except ValueError as exc:
            raise ValueError("Invalid channel type") from exc
        payload = MessageTemplateUpdate(
            name=name.strip(),
            channel_type=channel_enum,
            subject=subject.strip() if subject else None,
            body=body.strip(),
            is_active=bool(is_active),
        )
        message_templates.update(db, template_id, payload)
        return ActionResult(ok=True)
    except Exception as exc:
        return ActionResult(ok=False, error_detail=str(exc) or "Failed to update template")


def delete_message_template(
    db: Session,
    *,
    template_id: str,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> ActionResult:
    from app.services.crm.inbox.templates import message_templates

    try:
        if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
            return ActionResult(ok=False, error_detail="Forbidden")
        message_templates.delete(db, template_id)
        return ActionResult(ok=True)
    except Exception as exc:
        return ActionResult(ok=False, error_detail=str(exc) or "Failed to delete template")


def create_routing_rule(
    db: Session,
    *,
    team_id: str,
    channel_type: str,
    keywords: str | None,
    target_id: str | None,
    strategy: str | None,
    is_active: str | None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> ActionResult:
    try:
        if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
            return ActionResult(ok=False, error_detail="Forbidden")
        try:
            channel_enum = ChannelType(channel_type)
        except ValueError as exc:
            raise ValueError("Invalid channel type") from exc
        keywords_list = [k.strip() for k in (keywords or "").split(",") if k.strip()]
        rule_config = {
            "keywords": keywords_list,
            "target_id": target_id.strip() if target_id else None,
            "strategy": (strategy or "round_robin").strip(),
        }
        payload = RoutingRuleCreate(
            team_id=coerce_uuid(team_id),
            channel_type=channel_enum,
            rule_config=rule_config,
            is_active=bool(is_active),
        )
        crm_service.routing_rules.create(db, payload)
        return ActionResult(ok=True)
    except Exception as exc:
        return ActionResult(ok=False, error_detail=str(exc) or "Failed to create routing rule")


def update_routing_rule(
    db: Session,
    *,
    rule_id: str,
    channel_type: str,
    keywords: str | None,
    target_id: str | None,
    strategy: str | None,
    is_active: str | None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> ActionResult:
    try:
        if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
            return ActionResult(ok=False, error_detail="Forbidden")
        try:
            channel_enum = ChannelType(channel_type)
        except ValueError as exc:
            raise ValueError("Invalid channel type") from exc
        keywords_list = [k.strip() for k in (keywords or "").split(",") if k.strip()]
        rule_config = {
            "keywords": keywords_list,
            "target_id": target_id.strip() if target_id else None,
            "strategy": (strategy or "round_robin").strip(),
        }
        payload = RoutingRuleUpdate(
            channel_type=channel_enum,
            rule_config=rule_config,
            is_active=bool(is_active),
        )
        crm_service.routing_rules.update(db, rule_id, payload)
        return ActionResult(ok=True)
    except Exception as exc:
        return ActionResult(ok=False, error_detail=str(exc) or "Failed to update routing rule")


def delete_routing_rule(
    db: Session,
    *,
    rule_id: str,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> ActionResult:
    try:
        if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
            return ActionResult(ok=False, error_detail="Forbidden")
        crm_service.routing_rules.delete(db, rule_id)
        return ActionResult(ok=True)
    except Exception as exc:
        return ActionResult(ok=False, error_detail=str(exc) or "Failed to delete routing rule")
