"""Admin actions for CRM inbox settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain, SettingValueType
from app.schemas.crm.team import AgentCreate, AgentTeamCreate, TeamCreate
from app.schemas.settings import DomainSettingUpdate
from app.services import crm as crm_service
from app.services import domain_settings as domain_settings_service
from app.services import settings_spec
from app.services.common import coerce_uuid


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    error_detail: str | None = None


@dataclass(frozen=True)
class NotificationSettingsResult:
    ok: bool
    error_detail: str | None = None


def _coerce_value_json(value: object | None) -> dict[Any, Any] | list[Any] | bool | int | str | None:
    if isinstance(value, (dict, list, bool, int, str)):
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
) -> NotificationSettingsResult:
    try:
        reminder_delay = _coerce_int(
            "crm_inbox_reply_reminder_delay_seconds", reminder_delay_seconds
        )
        repeat_enabled = bool(reminder_repeat_enabled)
        reminder_repeat_interval = _coerce_int(
            "crm_inbox_reply_reminder_repeat_interval_seconds",
            reminder_repeat_interval_seconds,
        )
        auto_dismiss_seconds = _coerce_int(
            "crm_inbox_notification_auto_dismiss_seconds",
            notification_auto_dismiss_seconds,
        )
        settings_service = domain_settings_service.DomainSettings(
            SettingDomain.notification
        )

        spec = settings_spec.get_spec(
            SettingDomain.notification, "crm_inbox_reply_reminder_delay_seconds"
        )
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

        spec = settings_spec.get_spec(
            SettingDomain.notification, "crm_inbox_reply_reminder_repeat_enabled"
        )
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
            value_text, value_json = settings_spec.normalize_for_db(
                spec, reminder_repeat_interval
            )
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
            value_text, value_json = settings_spec.normalize_for_db(
                spec, auto_dismiss_seconds
            )
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


def create_team(db: Session, *, name: str, notes: str | None) -> ActionResult:
    try:
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
) -> ActionResult:
    try:
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
) -> ActionResult:
    try:
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
