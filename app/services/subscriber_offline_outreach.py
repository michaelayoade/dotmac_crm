from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import false, or_, select
from sqlalchemy.orm import Session

from app.models.connector import ConnectorConfig, ConnectorType
from app.models.crm.conversation import Conversation
from app.models.crm.enums import ChannelType, ConversationStatus, MessageStatus
from app.models.domain_settings import SettingValueType
from app.models.integration import IntegrationTarget
from app.models.person import ChannelType as PersonChannelType
from app.models.person import Person, PersonChannel
from app.models.scheduler import ScheduledTask, ScheduleType
from app.models.subscriber import Subscriber
from app.models.subscriber_outreach import SubscriberOfflineOutreachLog, SubscriberStationMapping
from app.models.tickets import Ticket, TicketStatus
from app.schemas.crm.conversation import ConversationCreate
from app.schemas.crm.inbox import InboxSendRequest
from app.schemas.settings import DomainSettingUpdate
from app.services import settings_spec, splynx, subscriber_reports, zabbix
from app.services.common import coerce_uuid
from app.services.crm import inbox as inbox_service
from app.services.crm.conversations.service import Conversations, resolve_open_conversation
from app.services.crm.inbox.summaries import recompute_conversation_summary
from app.services.crm.inbox.whatsapp_templates import list_whatsapp_templates
from app.services.domain_settings import notification_settings
from app.services.person_identity import ensure_person_channel

DEFAULT_TIMEZONE = "Africa/Lagos"
DEFAULT_TEMPLATE = (
    "Hello {first_name}, we noticed your Dotmac service was offline in the last 24 hours. "
    "If you need help getting back online, reply here and we will assist."
)
OFFLINE_OUTREACH_TASK_NAME = "app.tasks.subscriber_outreach.run_daily_offline_outreach"
OPEN_TICKET_STATUSES = {
    TicketStatus.new,
    TicketStatus.open,
    TicketStatus.pending,
    TicketStatus.waiting_on_customer,
    TicketStatus.lastmile_rerun,
    TicketStatus.site_under_construction,
    TicketStatus.on_hold,
}
ACTIVE_CONVERSATION_STATUSES = {ConversationStatus.open, ConversationStatus.pending}
UP_STATES = {"up", "online", "ok", "reachable", "success"}
DOWN_STATES = {"down", "offline", "critical", "unreachable", "failed"}
SITE_TOKEN_ALIASES = {
    "dloko": "lokogoma",
    "dgwarimpa": "gwarimpa",
    "dgwarinpa": "gwarimpa",
    "dmpape": "mpape",
    "gw": "gwarimpa",
    "karasana": "karsana",
    "gwarinpa": "gwarimpa",
}
EXACT_STATION_TITLE_ALIASES = {
    "dlifecamp1": "DLIFECAMP AP-1",
}


@dataclass(frozen=True)
class OutreachConfig:
    enabled: bool
    interval_seconds: int
    local_time: str
    timezone: str
    channel: str
    channel_target_id: str | None
    cooldown_hours: int
    message_template: str
    whatsapp_template_payload: dict[str, Any] | None
    last_completed_date: str | None


@dataclass(frozen=True)
class MonitoringMatch:
    normalized_station_key: str
    device_id: str | None
    title: str | None
    ping_state: str | None
    snmp_state: str | None
    station_status: str
    match_method: str
    match_confidence: str


def _coerce_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _coerce_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _coerce_text(value: object | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _normalize_station_key(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    compact = re.sub(r"[^a-z0-9]+", "", text)
    return compact


def _extract_station_code(value: str | None) -> str | None:
    text = str(value or "").upper()
    compound = re.search(r"\b([A-Z]+(?:-[A-Z]+)+)\s*-?(\d+)\b", text)
    if compound:
        prefix = compound.group(1).replace("-", "")
        return f"{prefix}{compound.group(2)}".lower()
    match = re.search(r"\b([A-Z]{1,8})\s*-\s*(\d+)\b", text)
    if match:
        return f"{match.group(1)}{match.group(2)}".lower()
    alt = re.search(r"\b([A-Z]{1,8}\d+)\b", re.sub(r"[^A-Z0-9]+", " ", text))
    if alt:
        return alt.group(1).lower()
    return None


def _extract_site_tokens(value: str | None) -> list[str]:
    text = str(value or "").lower()
    return re.findall(r"[a-z]{3,}", text)


def _site_token_candidates(value: str | None) -> list[str]:
    ignored = {"port", "master", "slave", "huawei", "gpon", "olt", "access", "switch"}
    tokens = [token for token in _extract_site_tokens(value) if token not in ignored]
    ordered: list[str] = []
    for token in tokens:
        canonical = SITE_TOKEN_ALIASES.get(token, token)
        if canonical not in ordered:
            ordered.append(canonical)
    return ordered


def _is_olt_style_label(value: str | None) -> bool:
    text = str(value or "").lower()
    return "olt" in text and "gpon" not in text


def _is_gpon_style_label(value: str | None) -> bool:
    return "gpon" in str(value or "").lower()


def _prefer_family_match(
    rows: list[dict[str, Any]],
    *,
    base_station_label: str,
) -> MonitoringMatch | None:
    if not rows:
        return None

    if _is_olt_style_label(base_station_label):
        for row in rows:
            title = _coerce_text(row.get("title") or row.get("name")) or ""
            lowered = title.lower()
            if "huawei olt" in lowered or (" olt " in f" {lowered} "):
                return _monitoring_match_from_row(row, method="site_family_olt", confidence="medium")

    if _is_gpon_style_label(base_station_label):
        for row in rows:
            title = _coerce_text(row.get("title") or row.get("name")) or ""
            if "gpon-" in title.lower():
                return _monitoring_match_from_row(row, method="site_family_gpon", confidence="medium")

    for row in rows:
        title = _coerce_text(row.get("title") or row.get("name")) or ""
        if "access" in title.lower():
            return _monitoring_match_from_row(row, method="site_family_access", confidence="medium")

    return None


def _resolve_timezone(value: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(str(value or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE)
    except Exception:
        return ZoneInfo(DEFAULT_TIMEZONE)


def _parse_local_time(value: str | None) -> time:
    text = str(value or "10:00").strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.time().replace(second=0, microsecond=0)
        except ValueError:
            continue
    return time(hour=10, minute=0)


def _load_config(db: Session) -> OutreachConfig:
    domain = notification_settings.domain
    if domain is None:
        raise RuntimeError("Notification settings domain is not configured")
    whatsapp_template_payload = settings_spec.resolve_value(
        db,
        domain,
        "subscriber_offline_outreach_whatsapp_template_payload",
    )
    return OutreachConfig(
        enabled=_coerce_bool(
            settings_spec.resolve_value(db, domain, "subscriber_offline_outreach_enabled"),
            False,
        ),
        interval_seconds=max(
            _coerce_int(
                settings_spec.resolve_value(
                    db,
                    domain,
                    "subscriber_offline_outreach_interval_seconds",
                ),
                3600,
            ),
            300,
        ),
        local_time=str(settings_spec.resolve_value(db, domain, "subscriber_offline_outreach_local_time") or "10:00"),
        timezone=str(
            settings_spec.resolve_value(db, domain, "subscriber_offline_outreach_timezone") or DEFAULT_TIMEZONE
        ),
        channel=str(
            settings_spec.resolve_value(db, domain, "subscriber_offline_outreach_channel") or "whatsapp"
        ).strip()
        or "whatsapp",
        channel_target_id=_coerce_text(
            settings_spec.resolve_value(
                db,
                domain,
                "subscriber_offline_outreach_channel_target_id",
            )
        ),
        cooldown_hours=max(
            _coerce_int(
                settings_spec.resolve_value(
                    db,
                    domain,
                    "subscriber_offline_outreach_cooldown_hours",
                ),
                72,
            ),
            0,
        ),
        message_template=str(
            settings_spec.resolve_value(
                db,
                domain,
                "subscriber_offline_outreach_message_template",
            )
            or DEFAULT_TEMPLATE
        ),
        whatsapp_template_payload=whatsapp_template_payload if isinstance(whatsapp_template_payload, dict) else None,
        last_completed_date=_coerce_text(
            settings_spec.resolve_value(
                db,
                domain,
                "subscriber_offline_outreach_last_completed_date",
            )
        ),
    )


def _monitoring_state(value: object | None) -> str | None:
    lowered = str(value or "").strip().lower()
    return lowered or None


def _classify_station_status(ping_state: str | None, snmp_state: str | None) -> str:
    for state in (ping_state, snmp_state):
        if state in DOWN_STATES:
            return "down"
    for state in (ping_state, snmp_state):
        if state in UP_STATES:
            return "up"
    return "unknown"


def _build_monitoring_indexes(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    by_normalized: dict[str, dict[str, Any]] = {}
    by_code: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        title = _coerce_text(row.get("title") or row.get("name"))
        if not title:
            continue
        normalized = _normalize_station_key(title)
        if normalized and normalized not in by_normalized:
            by_normalized[normalized] = row
        code = _extract_station_code(title)
        if code:
            by_code.setdefault(code, []).append(row)
    return by_normalized, by_code


def _monitoring_match_from_row(row: dict[str, Any], *, method: str, confidence: str) -> MonitoringMatch:
    title = _coerce_text(row.get("title") or row.get("name"))
    ping_state = _monitoring_state(row.get("ping_state") or row.get("ping"))
    snmp_state = _monitoring_state(row.get("snmp_state") or row.get("status"))
    return MonitoringMatch(
        normalized_station_key=_normalize_station_key(title),
        device_id=_coerce_text(row.get("id")),
        title=title,
        ping_state=ping_state,
        snmp_state=snmp_state,
        station_status=_classify_station_status(ping_state, snmp_state),
        match_method=method,
        match_confidence=confidence,
    )


def _fetch_monitoring_rows(db: Session) -> list[dict[str, Any]]:
    rows = zabbix.fetch_monitoring_devices(db)
    if rows:
        return rows
    return splynx.fetch_monitoring_devices(db)


def _resolve_monitoring_match(
    db: Session,
    *,
    base_station_label: str,
    monitoring_rows: list[dict[str, Any]],
    by_normalized: dict[str, dict[str, Any]],
    by_code: dict[str, list[dict[str, Any]]],
) -> MonitoringMatch | None:
    raw_label = str(base_station_label).strip()
    if not raw_label:
        return None

    existing = db.execute(
        select(SubscriberStationMapping).where(
            SubscriberStationMapping.raw_customer_base_station == raw_label,
            SubscriberStationMapping.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if existing and existing.monitoring_title:
        normalized_existing = _normalize_station_key(existing.monitoring_title)
        row = by_normalized.get(normalized_existing)
        if row is not None:
            match = _monitoring_match_from_row(
                row,
                method=str(existing.match_method or "stored"),
                confidence=str(existing.match_confidence or "confirmed"),
            )
            _refresh_station_mapping(db, existing=existing, label=raw_label, match=match)
            return match

    normalized = _normalize_station_key(raw_label)
    exact_title_alias = EXACT_STATION_TITLE_ALIASES.get(normalized)
    if exact_title_alias:
        aliased_row = by_normalized.get(_normalize_station_key(exact_title_alias))
        if aliased_row is not None:
            match = _monitoring_match_from_row(aliased_row, method="exact_title_alias", confidence="high")
            _refresh_station_mapping(db, existing=existing, label=raw_label, match=match)
            return match

    if normalized and normalized in by_normalized:
        match = _monitoring_match_from_row(by_normalized[normalized], method="exact_normalized", confidence="high")
        _refresh_station_mapping(db, existing=existing, label=raw_label, match=match)
        return match

    code = _extract_station_code(raw_label)
    if code:
        candidates = by_code.get(code) or []
        if len(candidates) == 1:
            match = _monitoring_match_from_row(candidates[0], method="station_code", confidence="high")
            _refresh_station_mapping(db, existing=existing, label=raw_label, match=match)
            return match

    site_tokens = _site_token_candidates(raw_label)
    if site_tokens:
        site_candidates: list[dict[str, Any]] = []
        for row in monitoring_rows:
            title = _coerce_text(row.get("title") or row.get("name")) or ""
            lowered = title.lower()
            if any(token in lowered for token in site_tokens):
                site_candidates.append(row)
        family_match = _prefer_family_match(site_candidates, base_station_label=raw_label)
        if family_match is not None:
            _refresh_station_mapping(db, existing=existing, label=raw_label, match=family_match)
            return family_match

    for monitoring_normalized, row in by_normalized.items():
        if normalized and (normalized in monitoring_normalized or monitoring_normalized in normalized):
            match = _monitoring_match_from_row(row, method="contains", confidence="medium")
            _refresh_station_mapping(db, existing=existing, label=raw_label, match=match)
            return match
    return None


def _refresh_station_mapping(
    db: Session,
    *,
    existing: SubscriberStationMapping | None,
    label: str,
    match: MonitoringMatch,
) -> None:
    mapping = existing or SubscriberStationMapping(
        raw_customer_base_station=label,
        normalized_station_key=match.normalized_station_key,
    )
    mapping.normalized_station_key = match.normalized_station_key
    mapping.monitoring_device_id = match.device_id
    mapping.monitoring_title = match.title
    mapping.match_method = match.match_method
    mapping.match_confidence = match.match_confidence
    mapping.last_verified_at = datetime.now(UTC)
    mapping.is_active = True
    if existing is None:
        db.add(mapping)
    db.flush()


def _resolve_whatsapp_target(db: Session, configured_target_id: str | None) -> IntegrationTarget | None:
    if configured_target_id:
        target = db.get(IntegrationTarget, coerce_uuid(configured_target_id))
        if target and target.is_active and target.connector_config_id:
            config = db.get(ConnectorConfig, target.connector_config_id)
            if config and config.is_active and config.connector_type == ConnectorType.whatsapp:
                return target
    return (
        db.query(IntegrationTarget)
        .join(ConnectorConfig, IntegrationTarget.connector_config_id == ConnectorConfig.id)
        .filter(IntegrationTarget.is_active.is_(True))
        .filter(ConnectorConfig.is_active.is_(True))
        .filter(ConnectorConfig.connector_type == ConnectorType.whatsapp)
        .order_by(IntegrationTarget.created_at.asc())
        .first()
    )


def _resolve_whatsapp_person_channel(db: Session, person: Person | None) -> PersonChannel | None:
    if person is None:
        return None
    existing = (
        db.query(PersonChannel)
        .filter(PersonChannel.person_id == person.id)
        .filter(PersonChannel.channel_type == PersonChannelType.whatsapp)
        .order_by(PersonChannel.is_primary.desc(), PersonChannel.created_at.asc())
        .first()
    )
    if existing:
        return existing

    fallback = (
        db.query(PersonChannel)
        .filter(PersonChannel.person_id == person.id)
        .filter(PersonChannel.channel_type.in_([PersonChannelType.phone, PersonChannelType.sms]))
        .order_by(PersonChannel.is_primary.desc(), PersonChannel.created_at.asc())
        .first()
    )
    if fallback and fallback.address:
        channel, _created = ensure_person_channel(db, person, PersonChannelType.whatsapp, fallback.address)
        return channel

    if person.phone:
        channel, _created = ensure_person_channel(db, person, PersonChannelType.whatsapp, person.phone)
        return channel
    return None


def _render_message(
    template: str, *, person: Person | None, subscriber: Subscriber | None, base_station_label: str | None
) -> str:
    first_name = ""
    if person and person.first_name:
        first_name = person.first_name.strip()
    if not first_name and person and person.display_name:
        first_name = person.display_name.strip().split()[0]
    replacements = {
        "{first_name}": first_name or "there",
        "{name}": (
            person.display_name
            if person and person.display_name
            else f"{person.first_name or ''} {person.last_name or ''}".strip()
            if person
            else "there"
        ),
        "{subscriber_number}": str(subscriber.subscriber_number or "") if subscriber else "",
        "{base_station}": str(base_station_label or ""),
    }
    message = template or DEFAULT_TEMPLATE
    for token, value in replacements.items():
        message = message.replace(token, str(value or ""))
    return message.strip()


def _whatsapp_template_token_values(
    *,
    person: Person | None,
    subscriber: Subscriber | None,
    base_station_label: str | None,
) -> dict[int, str]:
    first_name = ""
    if person and person.first_name:
        first_name = person.first_name.strip()
    if not first_name and person and person.display_name:
        first_name = person.display_name.strip().split()[0]
    full_name = (
        person.display_name
        if person and person.display_name
        else f"{person.first_name or ''} {person.last_name or ''}".strip()
        if person
        else ""
    )
    return {
        1: first_name or "there",
        2: str(subscriber.subscriber_number or "") if subscriber else "",
        3: str(base_station_label or ""),
        4: full_name or first_name or "there",
    }


def _whatsapp_template_named_values(
    *,
    person: Person | None,
    subscriber: Subscriber | None,
    base_station_label: str | None,
) -> dict[str, str]:
    indexed = _whatsapp_template_token_values(
        person=person,
        subscriber=subscriber,
        base_station_label=base_station_label,
    )
    return {
        "first_name": indexed[1],
        "subscriber_number": indexed[2],
        "base_station": indexed[3],
        "name": indexed[4],
    }


def _extract_template_placeholders(text: str | None) -> list[str]:
    if not text:
        return []
    placeholders: list[str] = []
    for raw in re.findall(r"{{\s*([A-Za-z_][A-Za-z0-9_]*|\d+)\s*}}", text):
        value = raw.strip()
        if value and value not in placeholders:
            placeholders.append(value)
    return placeholders


def _extract_template_placeholder_indexes(text: str | None) -> list[int]:
    indexes: list[int] = []
    for raw in _extract_template_placeholders(text):
        if not raw.isdigit():
            continue
        value = int(raw)
        if value not in indexes:
            indexes.append(value)
    return indexes


def _render_whatsapp_template_text(text: str | None, *, token_values: dict[str, str]) -> str:
    rendered = str(text or "")
    for key, value in token_values.items():
        rendered = re.sub(r"{{\s*" + re.escape(str(key)) + r"\s*}}", value, rendered)
    return rendered.strip()


def _template_placeholder_keys_from_components(components: object) -> list[str]:
    if not isinstance(components, list):
        return []
    keys: list[str] = []
    for component in components:
        if not isinstance(component, dict):
            continue
        for key in _extract_template_placeholders(str(component.get("text") or "")):
            if key not in keys:
                keys.append(key)
    return sorted(keys, key=lambda value: (0, int(value)) if value.isdigit() else (1, value))


def _render_saved_template_parameter(
    value: object,
    *,
    person: Person | None,
    subscriber: Subscriber | None,
    base_station_label: str | None,
) -> str:
    rendered = str(value or "")
    for token, replacement in _whatsapp_template_named_values(
        person=person,
        subscriber=subscriber,
        base_station_label=base_station_label,
    ).items():
        rendered = rendered.replace("{" + token + "}", replacement)
    return rendered.strip()


def _effective_whatsapp_template_parameters(
    template_payload: dict[str, Any] | None,
    *,
    person: Person | None,
    subscriber: Subscriber | None,
    base_station_label: str | None,
) -> dict[str, str]:
    indexed_defaults = _whatsapp_template_token_values(
        person=person,
        subscriber=subscriber,
        base_station_label=base_station_label,
    )
    defaults: dict[str, str] = {str(index): value for index, value in indexed_defaults.items()}
    defaults.update(
        _whatsapp_template_named_values(
            person=person,
            subscriber=subscriber,
            base_station_label=base_station_label,
        )
    )
    if not isinstance(template_payload, dict):
        return defaults
    configured = template_payload.get("parameter_values")
    if not isinstance(configured, dict):
        return defaults

    values = dict(defaults)
    for raw_key, raw_value in configured.items():
        key = str(raw_key or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*|\d+", key):
            continue
        rendered = _render_saved_template_parameter(
            raw_value,
            person=person,
            subscriber=subscriber,
            base_station_label=base_station_label,
        )
        if rendered:
            values[key] = rendered
    return values


def _build_whatsapp_template_components(
    template_payload: dict[str, Any] | None,
    *,
    person: Person | None,
    subscriber: Subscriber | None,
    base_station_label: str | None,
) -> list[dict[str, Any]] | None:
    if not isinstance(template_payload, dict):
        return None
    components = template_payload.get("components")
    if not isinstance(components, list):
        return None

    token_values = _effective_whatsapp_template_parameters(
        template_payload,
        person=person,
        subscriber=subscriber,
        base_station_label=base_station_label,
    )
    outbound_components: list[dict[str, Any]] = []
    for component in components:
        if not isinstance(component, dict):
            continue
        component_type = str(component.get("type") or "").upper()
        if component_type not in {"HEADER", "BODY"}:
            continue
        if component_type == "HEADER" and str(component.get("format") or "").upper() not in {"", "TEXT"}:
            continue
        text = str(component.get("text") or "")
        placeholders = _extract_template_placeholders(text)
        if not placeholders:
            continue
        parameters = []
        for placeholder in placeholders:
            parameter = {"type": "text", "text": token_values.get(placeholder, "")}
            if not placeholder.isdigit():
                parameter["parameter_name"] = placeholder
            parameters.append(parameter)
        outbound_components.append(
            {
                "type": component_type.lower(),
                "parameters": parameters,
            }
        )
    return outbound_components or None


def _render_whatsapp_template_preview(
    template_payload: dict[str, Any] | None,
    *,
    person: Person | None,
    subscriber: Subscriber | None,
    base_station_label: str | None,
) -> str:
    if not isinstance(template_payload, dict):
        return ""
    body = str(template_payload.get("body") or "").strip()
    if not body:
        components = template_payload.get("components")
        if isinstance(components, list):
            for component in components:
                if isinstance(component, dict) and str(component.get("type") or "").upper() == "BODY":
                    body = str(component.get("text") or "").strip()
                    break
    if not body:
        return ""
    return _render_whatsapp_template_text(
        body,
        token_values=_effective_whatsapp_template_parameters(
            template_payload,
            person=person,
            subscriber=subscriber,
            base_station_label=base_station_label,
        ),
    )


def _cleanup_failed_outreach_conversation(db: Session, conversation: Conversation | None, *, reason: str) -> None:
    if conversation is None:
        return
    existing = db.get(Conversation, conversation.id)
    if existing is None:
        return
    metadata = dict(existing.metadata_ or {})
    metadata["outreach_send_failed"] = True
    metadata["outreach_failure_reason"] = reason
    metadata["outreach_failure_at"] = datetime.now(UTC).isoformat()
    existing.metadata_ = metadata
    existing.status = ConversationStatus.resolved
    existing.resolved_at = datetime.now(UTC)
    db.flush()
    recompute_conversation_summary(db, str(existing.id))
    db.commit()


def _validate_whatsapp_target_for_settings(db: Session, target_id: str) -> IntegrationTarget:
    target = db.get(IntegrationTarget, coerce_uuid(target_id))
    if not target or not target.is_active or not target.connector_config_id:
        raise ValueError("Select an active WhatsApp Send From target.")
    connector = db.get(ConnectorConfig, target.connector_config_id)
    if not connector or not connector.is_active or connector.connector_type != ConnectorType.whatsapp:
        raise ValueError("Selected Send From target is not an active WhatsApp connector.")
    return target


def _normalize_selected_whatsapp_template(
    db: Session,
    *,
    target: IntegrationTarget,
    template_name: str,
    template_language: str,
) -> dict[str, Any]:
    templates = list_whatsapp_templates(db, connector_config_id=str(target.connector_config_id))
    for template in templates:
        if str(template.get("status") or "").lower() != "approved":
            continue
        if str(template.get("name") or "").strip() != template_name:
            continue
        if str(template.get("language") or "").strip() != template_language:
            continue
        return {
            "name": str(template.get("name") or "").strip(),
            "language": str(template.get("language") or "").strip(),
            "status": str(template.get("status") or "").strip(),
            "category": str(template.get("category") or "").strip(),
            "body": str(template.get("body") or "").strip(),
            "components": template.get("components") if isinstance(template.get("components"), list) else [],
        }
    raise ValueError("Select an approved WhatsApp template for the chosen Send From target.")


def _normalize_whatsapp_template_parameter_values(value: object) -> dict[str, str]:
    raw = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("WhatsApp template parameters are invalid.") from exc
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, item in raw.items():
        parameter_key = str(key or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*|\d+", parameter_key):
            continue
        text_value = str(item or "").strip()
        if text_value:
            cleaned[parameter_key] = text_value
    return cleaned


def _sync_offline_outreach_scheduled_task(db: Session, *, interval_seconds: int, enabled: bool) -> None:
    task = (
        db.query(ScheduledTask)
        .filter(ScheduledTask.task_name == OFFLINE_OUTREACH_TASK_NAME)
        .order_by(ScheduledTask.created_at.desc())
        .first()
    )
    if task is None:
        task = ScheduledTask(
            name="subscriber_offline_outreach",
            task_name=OFFLINE_OUTREACH_TASK_NAME,
            schedule_type=ScheduleType.interval,
            interval_seconds=max(interval_seconds, 300),
            enabled=enabled,
        )
        db.add(task)
        db.commit()
        return
    task.name = "subscriber_offline_outreach"
    task.interval_seconds = max(interval_seconds, 300)
    task.enabled = enabled
    db.commit()


def get_outreach_settings_snapshot(db: Session) -> dict[str, Any]:
    config = _load_config(db)
    template_payload = config.whatsapp_template_payload if isinstance(config.whatsapp_template_payload, dict) else {}
    return {
        "enabled": config.enabled,
        "interval_seconds": config.interval_seconds,
        "local_time": config.local_time,
        "timezone": config.timezone,
        "channel_target_id": config.channel_target_id or "",
        "cooldown_hours": config.cooldown_hours,
        "template_name": str(template_payload.get("name") or "").strip(),
        "template_language": str(template_payload.get("language") or "").strip(),
        "template_body": str(template_payload.get("body") or "").strip(),
        "template_parameter_values": (
            template_payload.get("parameter_values")
            if isinstance(template_payload.get("parameter_values"), dict)
            else {}
        ),
        "template_parameter_indexes": _template_placeholder_keys_from_components(template_payload.get("components")),
        "template_payload": template_payload if template_payload else None,
    }


def save_outreach_settings(
    db: Session,
    *,
    local_time: str,
    timezone: str,
    channel_target_id: str,
    whatsapp_template_name: str,
    whatsapp_template_language: str,
    whatsapp_template_parameters: object | None = None,
) -> dict[str, Any]:
    parsed_time = _parse_local_time(local_time)
    timezone_value = str(timezone or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    _resolve_timezone(timezone_value)
    target = _validate_whatsapp_target_for_settings(db, channel_target_id)
    template_payload = _normalize_selected_whatsapp_template(
        db,
        target=target,
        template_name=str(whatsapp_template_name or "").strip(),
        template_language=str(whatsapp_template_language or "").strip(),
    )
    parameter_values = _normalize_whatsapp_template_parameter_values(whatsapp_template_parameters)
    if parameter_values:
        template_payload["parameter_values"] = parameter_values

    notification_settings.upsert_by_key(
        db,
        "subscriber_offline_outreach_local_time",
        DomainSettingUpdate(
            value_type=SettingValueType.string, value_text=parsed_time.strftime("%H:%M"), is_active=True
        ),
    )
    notification_settings.upsert_by_key(
        db,
        "subscriber_offline_outreach_timezone",
        DomainSettingUpdate(value_type=SettingValueType.string, value_text=timezone_value, is_active=True),
    )
    notification_settings.upsert_by_key(
        db,
        "subscriber_offline_outreach_channel",
        DomainSettingUpdate(value_type=SettingValueType.string, value_text="whatsapp", is_active=True),
    )
    notification_settings.upsert_by_key(
        db,
        "subscriber_offline_outreach_channel_target_id",
        DomainSettingUpdate(value_type=SettingValueType.string, value_text=str(target.id), is_active=True),
    )
    notification_settings.upsert_by_key(
        db,
        "subscriber_offline_outreach_interval_seconds",
        DomainSettingUpdate(value_type=SettingValueType.integer, value_text="300", is_active=True),
    )
    notification_settings.upsert_by_key(
        db,
        "subscriber_offline_outreach_enabled",
        DomainSettingUpdate(value_type=SettingValueType.boolean, value_text="true", is_active=True),
    )
    notification_settings.upsert_by_key(
        db,
        "subscriber_offline_outreach_whatsapp_template_payload",
        DomainSettingUpdate(value_type=SettingValueType.json, value_json=template_payload, is_active=True),
    )
    _sync_offline_outreach_scheduled_task(db, interval_seconds=300, enabled=True)
    return get_outreach_settings_snapshot(db)


def _load_subscriber_records(db: Session, subscriber_ids: list[UUID]) -> dict[str, Subscriber]:
    if not subscriber_ids:
        return {}
    rows = db.execute(select(Subscriber).where(Subscriber.id.in_(subscriber_ids))).scalars().all()
    return {str(row.id): row for row in rows}


def _load_people(db: Session, person_ids: list[UUID]) -> dict[str, Person]:
    if not person_ids:
        return {}
    rows = db.execute(select(Person).where(Person.id.in_(person_ids))).scalars().all()
    return {str(row.id): row for row in rows}


def _open_ticket_subscribers(
    db: Session, subscriber_ids: list[UUID], person_ids: list[UUID]
) -> tuple[set[str], set[str]]:
    if not subscriber_ids and not person_ids:
        return set(), set()
    rows = db.execute(
        select(Ticket.subscriber_id, Ticket.customer_person_id).where(
            Ticket.is_active.is_(True),
            Ticket.status.in_(OPEN_TICKET_STATUSES),
            or_(
                Ticket.subscriber_id.in_(subscriber_ids) if subscriber_ids else false(),
                Ticket.customer_person_id.in_(person_ids) if person_ids else false(),
            ),
        )
    ).all()
    subscriber_hits = {str(subscriber_id) for subscriber_id, _person_id in rows if subscriber_id}
    person_hits = {str(person_id) for _subscriber_id, person_id in rows if person_id}
    return subscriber_hits, person_hits


def _open_conversation_people(db: Session, person_ids: list[UUID]) -> set[str]:
    if not person_ids:
        return set()
    rows = db.execute(
        select(Conversation.person_id).where(
            Conversation.is_active.is_(True),
            Conversation.status.in_(ACTIVE_CONVERSATION_STATUSES),
            Conversation.person_id.in_(person_ids),
        )
    ).all()
    return {str(person_id) for (person_id,) in rows if person_id}


def _recently_contacted_subscribers(db: Session, subscriber_ids: list[UUID], *, hours: int) -> set[str]:
    if not subscriber_ids or hours <= 0:
        return set()
    threshold = datetime.now(UTC) - timedelta(hours=hours)
    rows = db.execute(
        select(SubscriberOfflineOutreachLog.subscriber_id).where(
            SubscriberOfflineOutreachLog.subscriber_id.in_(subscriber_ids),
            SubscriberOfflineOutreachLog.sent_at.is_not(None),
            SubscriberOfflineOutreachLog.sent_at >= threshold,
            SubscriberOfflineOutreachLog.decision_status == "sent",
            SubscriberOfflineOutreachLog.is_active.is_(True),
        )
    ).all()
    return {str(subscriber_id) for (subscriber_id,) in rows if subscriber_id}


def _write_outreach_log(
    db: Session,
    *,
    run_local_date: date,
    subscriber: Subscriber | None,
    person: Person | None,
    customer: dict[str, Any] | None,
    base_station_label: str | None,
    match: MonitoringMatch | None,
    decision_status: str,
    decision_reason: str | None,
    message_template: str,
    conversation_id: UUID | None = None,
    message_id: UUID | None = None,
    channel_target_id: UUID | None = None,
    sent_at: datetime | None = None,
) -> None:
    customer_name = _coerce_text((customer or {}).get("name"))
    external_customer_id = _coerce_text((customer or {}).get("id")) or (
        str(subscriber.external_id or "").strip() if subscriber else ""
    )
    subscriber_number = _coerce_text((customer or {}).get("login")) or (
        str(subscriber.subscriber_number or "").strip() if subscriber else None
    )
    log = SubscriberOfflineOutreachLog(
        subscriber_id=subscriber.id if subscriber else None,
        person_id=person.id if person else None,
        conversation_id=conversation_id,
        message_id=message_id,
        channel_target_id=channel_target_id,
        run_local_date=run_local_date,
        external_customer_id=external_customer_id or "",
        subscriber_number=subscriber_number,
        customer_name=customer_name,
        base_station_label=_coerce_text(base_station_label),
        normalized_station_key=match.normalized_station_key if match else _normalize_station_key(base_station_label),
        monitoring_device_id=match.device_id if match else None,
        monitoring_title=match.title if match else None,
        monitoring_ping_state=match.ping_state if match else None,
        monitoring_snmp_state=match.snmp_state if match else None,
        station_status=match.station_status if match else None,
        decision_status=decision_status,
        decision_reason=decision_reason,
        message_template=message_template,
        sent_at=sent_at,
        is_active=True,
    )
    db.add(log)
    db.commit()


def _already_completed_today(config: OutreachConfig, run_local_date: date) -> bool:
    return str(config.last_completed_date or "").strip() == run_local_date.isoformat()


def _mark_completed(db: Session, run_local_date: date) -> None:
    notification_settings.upsert_by_key(
        db,
        "subscriber_offline_outreach_last_completed_date",
        DomainSettingUpdate(
            value_type=SettingValueType.string,
            value_text=run_local_date.isoformat(),
            is_active=True,
        ),
    )


def enrich_rows_with_station_status(
    db: Session,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not rows:
        return rows

    subscriber_uuid_ids: list[UUID] = []
    for row in rows:
        try:
            subscriber_uuid_ids.append(UUID(str(row.get("subscriber_id") or "").strip()))
        except (TypeError, ValueError):
            continue
    subscribers_by_id = _load_subscriber_records(db, subscriber_uuid_ids)
    customers = splynx.fetch_customers(db)
    customer_by_external_id = {
        str(customer.get("id") or "").strip(): customer
        for customer in customers
        if isinstance(customer, dict) and str(customer.get("id") or "").strip()
    }
    customer_by_login = {
        str(customer.get("login") or "").strip(): customer
        for customer in customers
        if isinstance(customer, dict) and str(customer.get("login") or "").strip()
    }
    monitoring_rows = _fetch_monitoring_rows(db)
    by_normalized, by_code = _build_monitoring_indexes(monitoring_rows)

    for row in rows:
        row.setdefault("base_station", "")
        row.setdefault("station_status", "unknown")
        row.setdefault("station_monitoring_title", "")
        row.setdefault("station_ping_state", "")
        row.setdefault("station_snmp_state", "")

        subscriber = subscribers_by_id.get(str(row.get("subscriber_id") or "").strip())
        customer = None
        splynx_customer_id = str(row.get("splynx_customer_id") or "").strip()
        if splynx_customer_id:
            customer = customer_by_external_id.get(splynx_customer_id)
        if customer is None and subscriber and subscriber.external_id:
            customer = customer_by_external_id.get(str(subscriber.external_id).strip())
        if customer is None:
            customer = customer_by_login.get(str(row.get("splynx_login") or row.get("subscriber_number") or "").strip())

        base_station_label = _coerce_text(row.get("base_station"))
        if not base_station_label and customer is not None:
            base_station_label = _coerce_text(splynx.customer_base_station(customer))
        row["base_station"] = base_station_label or ""
        if not base_station_label:
            continue

        match = _resolve_monitoring_match(
            db,
            base_station_label=base_station_label,
            monitoring_rows=monitoring_rows,
            by_normalized=by_normalized,
            by_code=by_code,
        )
        if match is None:
            continue
        row["station_status"] = match.station_status
        row["station_monitoring_title"] = match.title or ""
        row["station_ping_state"] = match.ping_state or ""
        row["station_snmp_state"] = match.snmp_state or ""
    return rows


def run_daily_offline_outreach(
    db: Session,
    *,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    now = now_utc.astimezone(UTC) if now_utc else datetime.now(UTC)
    config = _load_config(db)
    local_zone = _resolve_timezone(config.timezone)
    local_now = now.astimezone(local_zone)
    run_local_date = local_now.date()
    scheduled_local_time = _parse_local_time(config.local_time)

    result: dict[str, Any] = {
        "status": "skipped",
        "run_local_date": run_local_date.isoformat(),
        "local_time": local_now.isoformat(),
        "evaluated": 0,
        "sent": 0,
        "skipped": 0,
        "failed": 0,
    }
    if not config.enabled:
        result["reason"] = "disabled"
        return result
    if config.channel != "whatsapp":
        result["reason"] = "unsupported_channel"
        return result
    if local_now.time().replace(second=0, microsecond=0) < scheduled_local_time:
        result["reason"] = "before_scheduled_time"
        return result
    if _already_completed_today(config, run_local_date):
        result["reason"] = "already_completed"
        return result

    offline_rows = subscriber_reports.online_customers_last_24h_rows(
        db,
        activity_segment="active_last24_not_online",
        limit=10000,
    )
    if not offline_rows:
        _mark_completed(db, run_local_date)
        result["status"] = "success"
        result["reason"] = "no_candidates"
        return result

    customers = splynx.fetch_customers(db)
    customer_by_external_id = {
        str(customer.get("id") or "").strip(): customer
        for customer in customers
        if isinstance(customer, dict) and str(customer.get("id") or "").strip()
    }
    customer_by_login = {
        str(customer.get("login") or "").strip(): customer
        for customer in customers
        if isinstance(customer, dict) and str(customer.get("login") or "").strip()
    }
    monitoring_rows = _fetch_monitoring_rows(db)
    by_normalized, by_code = _build_monitoring_indexes(monitoring_rows)

    subscriber_uuid_ids: list[UUID] = []
    for row in offline_rows:
        try:
            subscriber_uuid_ids.append(UUID(str(row.get("subscriber_id") or "").strip()))
        except (TypeError, ValueError):
            continue
    subscribers_by_id = _load_subscriber_records(db, subscriber_uuid_ids)
    person_uuid_ids = [subscriber.person_id for subscriber in subscribers_by_id.values() if subscriber.person_id]
    people_by_id = _load_people(db, person_uuid_ids)
    open_ticket_subscriber_ids, open_ticket_person_ids = _open_ticket_subscribers(
        db, subscriber_uuid_ids, person_uuid_ids
    )
    open_conversation_person_ids = _open_conversation_people(db, person_uuid_ids)
    cooldown_subscriber_ids = _recently_contacted_subscribers(db, subscriber_uuid_ids, hours=config.cooldown_hours)
    target = _resolve_whatsapp_target(db, config.channel_target_id)

    for row in offline_rows:
        result["evaluated"] += 1
        subscriber_id = str(row.get("subscriber_id") or "").strip()
        subscriber = subscribers_by_id.get(subscriber_id)
        person = people_by_id.get(str(subscriber.person_id)) if subscriber and subscriber.person_id else None

        customer = None
        if subscriber and subscriber.external_id:
            customer = customer_by_external_id.get(str(subscriber.external_id).strip())
        if customer is None and subscriber and subscriber.subscriber_number:
            customer = customer_by_login.get(str(subscriber.subscriber_number).strip())

        if customer is None:
            _write_outreach_log(
                db,
                run_local_date=run_local_date,
                subscriber=subscriber,
                person=person,
                customer=None,
                base_station_label=None,
                match=None,
                decision_status="skipped",
                decision_reason="customer_not_found_in_splynx",
                message_template=config.message_template,
            )
            result["skipped"] += 1
            continue

        base_station_label = _coerce_text(splynx.customer_base_station(customer))
        if not base_station_label:
            _write_outreach_log(
                db,
                run_local_date=run_local_date,
                subscriber=subscriber,
                person=person,
                customer=customer,
                base_station_label=None,
                match=None,
                decision_status="skipped",
                decision_reason="missing_base_station",
                message_template=config.message_template,
            )
            result["skipped"] += 1
            continue

        match = _resolve_monitoring_match(
            db,
            base_station_label=base_station_label,
            monitoring_rows=monitoring_rows,
            by_normalized=by_normalized,
            by_code=by_code,
        )
        if match is None:
            _write_outreach_log(
                db,
                run_local_date=run_local_date,
                subscriber=subscriber,
                person=person,
                customer=customer,
                base_station_label=base_station_label,
                match=None,
                decision_status="skipped",
                decision_reason="station_unmapped",
                message_template=config.message_template,
            )
            result["skipped"] += 1
            continue
        if match.station_status != "up":
            _write_outreach_log(
                db,
                run_local_date=run_local_date,
                subscriber=subscriber,
                person=person,
                customer=customer,
                base_station_label=base_station_label,
                match=match,
                decision_status="skipped",
                decision_reason="station_down",
                message_template=config.message_template,
            )
            result["skipped"] += 1
            continue
        if subscriber and str(subscriber.id) in open_ticket_subscriber_ids:
            _write_outreach_log(
                db,
                run_local_date=run_local_date,
                subscriber=subscriber,
                person=person,
                customer=customer,
                base_station_label=base_station_label,
                match=match,
                decision_status="skipped",
                decision_reason="open_ticket",
                message_template=config.message_template,
            )
            result["skipped"] += 1
            continue
        if person and str(person.id) in open_ticket_person_ids:
            _write_outreach_log(
                db,
                run_local_date=run_local_date,
                subscriber=subscriber,
                person=person,
                customer=customer,
                base_station_label=base_station_label,
                match=match,
                decision_status="skipped",
                decision_reason="open_ticket",
                message_template=config.message_template,
            )
            result["skipped"] += 1
            continue
        if person and str(person.id) in open_conversation_person_ids:
            _write_outreach_log(
                db,
                run_local_date=run_local_date,
                subscriber=subscriber,
                person=person,
                customer=customer,
                base_station_label=base_station_label,
                match=match,
                decision_status="skipped",
                decision_reason="open_conversation",
                message_template=config.message_template,
            )
            result["skipped"] += 1
            continue
        if subscriber and str(subscriber.id) in cooldown_subscriber_ids:
            _write_outreach_log(
                db,
                run_local_date=run_local_date,
                subscriber=subscriber,
                person=person,
                customer=customer,
                base_station_label=base_station_label,
                match=match,
                decision_status="skipped",
                decision_reason="cooldown_active",
                message_template=config.message_template,
            )
            result["skipped"] += 1
            continue
        if person is None:
            _write_outreach_log(
                db,
                run_local_date=run_local_date,
                subscriber=subscriber,
                person=None,
                customer=customer,
                base_station_label=base_station_label,
                match=match,
                decision_status="skipped",
                decision_reason="missing_person",
                message_template=config.message_template,
            )
            result["skipped"] += 1
            continue
        if target is None:
            _write_outreach_log(
                db,
                run_local_date=run_local_date,
                subscriber=subscriber,
                person=person,
                customer=customer,
                base_station_label=base_station_label,
                match=match,
                decision_status="failed",
                decision_reason="missing_whatsapp_target",
                message_template=config.message_template,
            )
            result["failed"] += 1
            continue

        person_channel = _resolve_whatsapp_person_channel(db, person)
        if person_channel is None:
            _write_outreach_log(
                db,
                run_local_date=run_local_date,
                subscriber=subscriber,
                person=person,
                customer=customer,
                base_station_label=base_station_label,
                match=match,
                decision_status="skipped",
                decision_reason="missing_whatsapp_channel",
                message_template=config.message_template,
            )
            result["skipped"] += 1
            continue

        template_payload = (
            config.whatsapp_template_payload if isinstance(config.whatsapp_template_payload, dict) else None
        )
        template_name = str((template_payload or {}).get("name") or "").strip() or None
        template_language = str((template_payload or {}).get("language") or "").strip() or None
        template_components = _build_whatsapp_template_components(
            template_payload,
            person=person,
            subscriber=subscriber,
            base_station_label=base_station_label,
        )
        if not template_name or not template_language:
            _write_outreach_log(
                db,
                run_local_date=run_local_date,
                subscriber=subscriber,
                person=person,
                customer=customer,
                base_station_label=base_station_label,
                match=match,
                decision_status="skipped",
                decision_reason="missing_whatsapp_template",
                message_template=config.message_template,
            )
            result["skipped"] += 1
            continue

        message_body = _render_whatsapp_template_preview(
            template_payload,
            person=person,
            subscriber=subscriber,
            base_station_label=base_station_label,
        )
        conversation = resolve_open_conversation(db, str(person.id))
        if conversation is not None:
            _write_outreach_log(
                db,
                run_local_date=run_local_date,
                subscriber=subscriber,
                person=person,
                customer=customer,
                base_station_label=base_station_label,
                match=match,
                decision_status="skipped",
                decision_reason="open_conversation",
                message_template=message_body,
            )
            result["skipped"] += 1
            continue

        conversation = Conversations.create(
            db,
            ConversationCreate(
                person_id=person.id,
                metadata_={
                    "automation_kind": "subscriber_offline_outreach",
                    "source_report": "online_last_24h",
                    "subscriber_id": str(subscriber.id) if subscriber else None,
                    "external_customer_id": str(customer.get("id") or "").strip() or None,
                    "base_station": base_station_label,
                    "preferred_channel_target_id": str(target.id),
                },
            ),
        )
        try:
            message = inbox_service.send_message(
                db,
                InboxSendRequest(
                    conversation_id=conversation.id,
                    channel_type=ChannelType.whatsapp,
                    channel_target_id=target.id,
                    person_channel_id=person_channel.id,
                    body=message_body,
                    whatsapp_template_name=template_name,
                    whatsapp_template_language=template_language,
                    whatsapp_template_components=template_components,
                ),
            )
        except Exception:
            _cleanup_failed_outreach_conversation(db, conversation, reason="send_exception")
            _write_outreach_log(
                db,
                run_local_date=run_local_date,
                subscriber=subscriber,
                person=person,
                customer=customer,
                base_station_label=base_station_label,
                match=match,
                decision_status="failed",
                decision_reason="send_exception",
                message_template=message_body,
                conversation_id=conversation.id,
                channel_target_id=target.id,
            )
            result["failed"] += 1
            continue

        if message.status == MessageStatus.failed:
            _cleanup_failed_outreach_conversation(db, conversation, reason="send_failed")
            _write_outreach_log(
                db,
                run_local_date=run_local_date,
                subscriber=subscriber,
                person=person,
                customer=customer,
                base_station_label=base_station_label,
                match=match,
                decision_status="failed",
                decision_reason="send_failed",
                message_template=message_body,
                conversation_id=conversation.id,
                message_id=message.id,
                channel_target_id=target.id,
            )
            result["failed"] += 1
            continue

        _write_outreach_log(
            db,
            run_local_date=run_local_date,
            subscriber=subscriber,
            person=person,
            customer=customer,
            base_station_label=base_station_label,
            match=match,
            decision_status="sent",
            decision_reason=None,
            message_template=message_body,
            conversation_id=conversation.id,
            message_id=message.id,
            channel_target_id=target.id,
            sent_at=datetime.now(UTC),
        )
        result["sent"] += 1

    _mark_completed(db, run_local_date)
    result["status"] = "success"
    return result
