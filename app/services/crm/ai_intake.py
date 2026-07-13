from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.metrics import observe_ai_intake_escalation, observe_ai_intake_result
from app.models.crm.ai_intake import AiIntakeConfig
from app.models.crm.conversation import Conversation, ConversationAssignment, ConversationTag, Message
from app.models.crm.enums import (
    ChannelType,
    ConversationPriority,
    ConversationStatus,
    MessageDirection,
)
from app.models.crm.team import CrmAgent, CrmAgentTeam, CrmTeam
from app.models.person import Gender, PartyStatus, Person
from app.schemas.crm.ai_intake import (
    AiIntakeConfigCreate,
    AiIntakeConfigUpdate,
    AiIntakeDepartmentMapping,
)
from app.schemas.crm.inbox import InboxSendRequest
from app.services.ai.client import AIClientError
from app.services.ai.gateway import ai_gateway
from app.services.common import coerce_uuid
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox import cache as inbox_cache
from app.services.crm.inbox import routing as inbox_routing
from app.services.crm.inbox.outbound import send_message

logger = logging.getLogger(__name__)

AI_INTAKE_METADATA_KEY = "ai_intake"
AI_INTAKE_HANDOFF_SENT_KEY = "handoff_sent"
AI_INTAKE_HANDOFF_FOLLOWUP_MINUTES = 15
AI_INTAKE_HANDOFF_REASSIGN_MINUTES = 15
AI_INTAKE_HANDOFF_MESSAGE_KIND = "handoff"
AI_INTAKE_PROFILE_UPDATE_PROMPT_KIND = "profile_update_prompt"
AI_INTAKE_HANDOFF_REASSURANCE_KIND = "handoff_reassurance"
AI_INTAKE_FOLLOWUP_QUESTION_KIND = "followup_question"
AI_INTAKE_PROFILE_REQUEST_KIND = "profile_request"
AI_INTAKE_PROFILE_RETRY_KIND = "profile_retry"
AI_INTAKE_PROFILE_NUDGE_KIND = "profile_nudge"
AI_INTAKE_PROFILE_STATUS = "awaiting_profile"
AI_INTAKE_PROFILE_MAX_INVALID_REPLIES = 1
AI_INTAKE_PROFILE_NUDGE_MINUTES = 20
AI_INTAKE_SEND_CLAIM_TTL_SECONDS = 300
AI_INTAKE_PENDING_STATES = {"pending", "awaiting_customer", "awaiting_timeout", AI_INTAKE_PROFILE_STATUS}
AI_INTAKE_TERMINAL_STATES = {"resolved", "escalated", "excluded", "human_assigned"}
AI_INTAKE_RECOVERABLE_FAILURE_TYPES = {
    "auth",
    "provider_billing",
    "rate_limit",
    "provider_5xx",
    "timeout",
    "dns_network",
    "tls_handshake",
    "connection_error",
    "network_error",
    "circuit_open",
}
AI_INTAKE_RECOVERY_MAX_ATTEMPTS = 1
AI_INTAKE_RECOVERY_LOOKBACK_HOURS = 24
AI_INTAKE_HANDOFF_STATE_NONE = "none"
AI_INTAKE_HANDOFF_STATE_AWAITING_AGENT = "awaiting_agent"
AI_INTAKE_HANDOFF_STATE_ASSIGNED = "assigned"
AI_INTAKE_HANDOFF_STATE_IN_PROGRESS = "in_progress"
AI_INTAKE_HANDOFF_STATE_COMPLETED = "completed"
AI_INTAKE_HANDOFF_ALLOWED_STATES = {
    AI_INTAKE_HANDOFF_STATE_NONE,
    AI_INTAKE_HANDOFF_STATE_AWAITING_AGENT,
    AI_INTAKE_HANDOFF_STATE_ASSIGNED,
    AI_INTAKE_HANDOFF_STATE_IN_PROGRESS,
    AI_INTAKE_HANDOFF_STATE_COMPLETED,
}
AI_INTAKE_ALLOWED_DEPARTMENTS = {
    "billing",
    "billing_payment",
    "billing_renewal",
    "billing_reactivation",
    "billing_adjustment",
    "billing_general",
    "support",
    "sales",
}
AI_INTAKE_DEPARTMENT_HINTS = {
    "billing": "General billing intent when the business uses a single billing queue.",
    "billing_payment": "Payment confirmations, payment failures, overpayment, account reactivation after payment.",
    "billing_renewal": "Subscription renewal, plan extension, multi-month renewal, purchase-style renewal decisions.",
    "billing_reactivation": "Restore or reactivate service after payment or billing hold.",
    "billing_adjustment": "Refunds, credits, compensation, invoice correction, billing adjustments.",
    "billing_general": "Other billing questions that do not clearly fit payment, renewal, reactivation, or adjustment.",
    "support": "Technical issues, outages, slow speed, engineer follow-up, existing service fault.",
    "sales": "New connection, coverage, pricing for new service, package inquiry, upgrade, new order.",
}
SUPPORTED_CHANNELS = {
    ChannelType.whatsapp,
    ChannelType.facebook_messenger,
    ChannelType.instagram_dm,
    ChannelType.chat_widget,
}
ENV_FLAG = "CRM_AI_PENDING_INTAKE_ENABLED"
_PROFILE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PROFILE_GENDER_VALUES = {
    "male": Gender.male,
    "female": Gender.female,
    "non-binary": Gender.non_binary,
    "other": Gender.other,
}


@dataclass(frozen=True)
class AiIntakeResult:
    handled: bool
    resolved: bool = False
    followup_sent: bool = False
    excluded: bool = False
    fallback_used: bool = False
    escalated: bool = False
    waiting_for_customer: bool = False


@dataclass(frozen=True)
class DepartmentRoutingSelection:
    team_id: uuid.UUID | None
    agent_id: uuid.UUID | None
    configured_agent_ids: tuple[str, ...]
    active_agent_ids: tuple[str, ...]
    reason: str
    routing_state: str


def _now() -> datetime:
    return datetime.now(UTC)


def _enabled_by_env() -> bool:
    return os.getenv(ENV_FLAG, "0").strip().lower() in {"1", "true", "yes", "on"}


def _coerce_channel_type(channel_type: ChannelType | str) -> ChannelType:
    if isinstance(channel_type, ChannelType):
        return channel_type
    return ChannelType(str(channel_type).strip())


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _coerce_json_list(raw: str | list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return raw
    if raw is None:
        return []
    parsed = json.loads(raw or "[]")
    if not isinstance(parsed, list):
        raise ValueError("Department mappings must be a JSON array")
    return parsed


def _normalize_mapping(mapping: AiIntakeDepartmentMapping) -> AiIntakeDepartmentMapping:
    data = mapping.model_dump()
    data["key"] = mapping.key.strip().lower()
    data["label"] = mapping.label.strip()
    return AiIntakeDepartmentMapping(**data)


def _validate_department_mappings(
    mappings: list[AiIntakeDepartmentMapping], *, require_team_ids: bool
) -> list[AiIntakeDepartmentMapping]:
    normalized: list[AiIntakeDepartmentMapping] = []
    seen: set[str] = set()
    for mapping in mappings:
        item = _normalize_mapping(mapping)
        if item.key not in AI_INTAKE_ALLOWED_DEPARTMENTS:
            allowed = ", ".join(sorted(AI_INTAKE_ALLOWED_DEPARTMENTS))
            raise ValueError(f"Department key '{item.key}' is invalid. Allowed values: {allowed}")
        if item.key in seen:
            raise ValueError(f"Duplicate department mapping: {item.key}")
        if require_team_ids and item.team_id is None:
            raise ValueError(f"Department '{item.key}' requires a team_id when AI intake is enabled")
        seen.add(item.key)
        normalized.append(item)
    return normalized


def _build_update_payload(
    *,
    scope_key: str,
    channel_type: ChannelType,
    enabled: Any,
    confidence_threshold: Any,
    allow_followup_questions: Any,
    max_clarification_turns: Any,
    escalate_after_minutes: Any,
    exclude_campaign_attribution: Any,
    fallback_team_id: str | None,
    instructions: str | None,
    department_mappings_json: str | list[dict[str, Any]] | None,
) -> AiIntakeConfigUpdate:
    parsed_mappings = _coerce_json_list(department_mappings_json)
    mappings = [AiIntakeDepartmentMapping(**item) for item in parsed_mappings]
    is_enabled = _coerce_bool(enabled)
    normalized_mappings = _validate_department_mappings(mappings, require_team_ids=is_enabled)
    if channel_type not in SUPPORTED_CHANNELS:
        raise ValueError("AI intake supports only non-email inboxes and chat widgets")
    if not scope_key.strip():
        raise ValueError("Scope key is required")
    if is_enabled and not normalized_mappings:
        raise ValueError("Enable AI intake only after configuring at least one department mapping")
    if is_enabled and not (fallback_team_id or "").strip():
        raise ValueError("A fallback live team is required when AI intake is enabled")
    return AiIntakeConfigUpdate(
        is_enabled=is_enabled,
        confidence_threshold=float(confidence_threshold or "0.75"),
        allow_followup_questions=_coerce_bool(allow_followup_questions),
        max_clarification_turns=int(max_clarification_turns or "1"),
        escalate_after_minutes=int(escalate_after_minutes or "5"),
        exclude_campaign_attribution=_coerce_bool(exclude_campaign_attribution),
        fallback_team_id=(coerce_uuid((fallback_team_id or "").strip()) if (fallback_team_id or "").strip() else None),
        instructions=(instructions or "").strip() or None,
        department_mappings=normalized_mappings,
    )


def save_ai_intake_config(
    db: Session,
    *,
    scope_key: str,
    channel_type: ChannelType | str,
    enabled: Any,
    confidence_threshold: Any,
    allow_followup_questions: Any,
    max_clarification_turns: Any,
    escalate_after_minutes: Any,
    exclude_campaign_attribution: Any,
    fallback_team_id: str | None,
    instructions: str | None,
    department_mappings_json: str | list[dict[str, Any]] | None,
) -> AiIntakeConfig:
    channel = _coerce_channel_type(channel_type)
    payload = _build_update_payload(
        scope_key=scope_key,
        channel_type=channel,
        enabled=enabled,
        confidence_threshold=confidence_threshold,
        allow_followup_questions=allow_followup_questions,
        max_clarification_turns=max_clarification_turns,
        escalate_after_minutes=escalate_after_minutes,
        exclude_campaign_attribution=exclude_campaign_attribution,
        fallback_team_id=fallback_team_id,
        instructions=instructions,
        department_mappings_json=department_mappings_json,
    )
    return upsert_config(db, payload, scope_key=scope_key.strip(), channel_type=channel)


def make_scope_key(
    *, channel_type: ChannelType, target_id: str | None = None, widget_config_id: str | None = None
) -> str | None:
    if channel_type == ChannelType.chat_widget:
        if not widget_config_id:
            return None
        return f"widget:{widget_config_id}"
    if not target_id:
        return None
    return f"target:{target_id}"


def list_configs(db: Session) -> list[AiIntakeConfig]:
    try:
        return db.query(AiIntakeConfig).order_by(AiIntakeConfig.created_at.asc()).all()
    except (DBAPIError, OperationalError, ProgrammingError) as exc:
        logger.warning("ai_intake_config_table_unavailable error=%s", exc)
        db.rollback()
        return []


def list_configs_by_scope(db: Session) -> dict[str, AiIntakeConfig]:
    return {config.scope_key: config for config in list_configs(db)}


def get_config_for_scope(db: Session, scope_key: str | None) -> AiIntakeConfig | None:
    if not scope_key:
        return None
    try:
        return db.query(AiIntakeConfig).filter(AiIntakeConfig.scope_key == scope_key).first()
    except (DBAPIError, OperationalError, ProgrammingError) as exc:
        logger.warning("ai_intake_config_lookup_unavailable scope_key=%s error=%s", scope_key, exc)
        db.rollback()
        return None


def _serialize_department_mappings(
    items: Sequence[AiIntakeDepartmentMapping | dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not items:
        return []
    serialized: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, AiIntakeDepartmentMapping):
            serialized.append(item.model_dump(mode="json"))
        elif isinstance(item, dict):
            serialized.append(AiIntakeDepartmentMapping(**item).model_dump(mode="json"))
        else:
            raise ValueError("Invalid department mapping payload")
    return serialized


def upsert_config(
    db: Session, payload: AiIntakeConfigCreate | AiIntakeConfigUpdate, *, scope_key: str, channel_type: ChannelType
) -> AiIntakeConfig:
    try:
        config = get_config_for_scope(db, scope_key)
        if not config:
            if not isinstance(payload, AiIntakeConfigCreate):
                create_payload = AiIntakeConfigCreate(
                    scope_key=scope_key,
                    channel_type=channel_type,
                    is_enabled=bool(payload.is_enabled),
                    confidence_threshold=payload.confidence_threshold or 0.75,
                    allow_followup_questions=(
                        True if payload.allow_followup_questions is None else payload.allow_followup_questions
                    ),
                    max_clarification_turns=payload.max_clarification_turns or 1,
                    escalate_after_minutes=payload.escalate_after_minutes or 5,
                    exclude_campaign_attribution=(
                        True if payload.exclude_campaign_attribution is None else payload.exclude_campaign_attribution
                    ),
                    fallback_team_id=payload.fallback_team_id,
                    instructions=payload.instructions,
                    department_mappings=payload.department_mappings or [],
                )
                payload = create_payload
            config = AiIntakeConfig(
                scope_key=payload.scope_key,
                channel_type=payload.channel_type,
                is_enabled=payload.is_enabled,
                confidence_threshold=payload.confidence_threshold,
                allow_followup_questions=payload.allow_followup_questions,
                max_clarification_turns=payload.max_clarification_turns,
                escalate_after_minutes=payload.escalate_after_minutes,
                exclude_campaign_attribution=payload.exclude_campaign_attribution,
                fallback_team_id=payload.fallback_team_id,
                instructions=payload.instructions,
                department_mappings=_serialize_department_mappings(payload.department_mappings),
            )
            db.add(config)
            db.commit()
            db.refresh(config)
            return config

        data = payload.model_dump(exclude_unset=True)
        if "department_mappings" in data and data["department_mappings"] is not None:
            data["department_mappings"] = _serialize_department_mappings(data["department_mappings"])
        for key, value in data.items():
            setattr(config, key, value)
        db.commit()
        db.refresh(config)
        return config
    except (DBAPIError, OperationalError, ProgrammingError) as exc:
        db.rollback()
        logger.warning("ai_intake_config_write_unavailable scope_key=%s error=%s", scope_key, exc)
        raise RuntimeError("AI intake table is not available. Run the latest migration first.") from exc


def _mapping_objects(config: AiIntakeConfig | None) -> list[AiIntakeDepartmentMapping]:
    if not config:
        return []
    raw = config.department_mappings if isinstance(config.department_mappings, list) else []
    return _validate_department_mappings([AiIntakeDepartmentMapping(**item) for item in raw], require_team_ids=False)


def _campaign_attribution(metadata: dict[str, Any] | None) -> bool:
    if not isinstance(metadata, dict):
        return False
    attribution = metadata.get("attribution")
    attr = attribution if isinstance(attribution, dict) else {}
    top_level_hits = {
        "campaign_id",
        "meta_leadgen_id",
        "gclid",
        "fbclid",
        "campaign",
    }
    if any(metadata.get(key) for key in top_level_hits):
        return True
    if any(attr.get(key) for key in ("campaign_id", "ad_id", "leadgen_id", "gclid", "fbclid", "utm_campaign")):
        return True
    source = str(attr.get("utm_source") or attr.get("source") or "").strip().lower()
    return source in {"meta", "facebook", "instagram", "google", "ads"}


def _merge_metadata(conversation: Conversation, message: Message) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if isinstance(conversation.metadata_, dict):
        merged.update(conversation.metadata_)
    if isinstance(message.metadata_, dict):
        merged.update(message.metadata_)
    return merged


def _default_handoff_state(status: Any) -> str:
    return AI_INTAKE_HANDOFF_STATE_AWAITING_AGENT if status == "resolved" else AI_INTAKE_HANDOFF_STATE_NONE


def _normalize_handoff_state(value: Any, *, status: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in AI_INTAKE_HANDOFF_ALLOWED_STATES:
        return normalized
    return _default_handoff_state(status)


def _with_handoff_defaults(state: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(state)
    normalized["handoff_state"] = _normalize_handoff_state(
        normalized.get("handoff_state"), status=normalized.get("status")
    )
    handoff_sent_at = _parse_timestamp(normalized.get("handoff_sent_at"))
    if handoff_sent_at is not None and not normalized.get("handoff_followup_due_at"):
        normalized["handoff_followup_due_at"] = _serialize_timestamp(
            handoff_sent_at + timedelta(minutes=AI_INTAKE_HANDOFF_FOLLOWUP_MINUTES)
        )
    return normalized


def _state(conversation: Conversation) -> dict[str, Any]:
    if not isinstance(conversation.metadata_, dict):
        return {}
    current = conversation.metadata_.get(AI_INTAKE_METADATA_KEY)
    return _with_handoff_defaults(current) if isinstance(current, dict) else {}


def _set_state(conversation: Conversation, state: dict[str, Any]) -> None:
    metadata = dict(conversation.metadata_ or {}) if isinstance(conversation.metadata_, dict) else {}
    metadata[AI_INTAKE_METADATA_KEY] = _with_handoff_defaults(state)
    conversation.metadata_ = metadata


def _history(db: Session, conversation: Conversation, limit: int = 12) -> list[Message]:
    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(limit)
        .all()
    )
    messages.reverse()
    return messages


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _deadline_for_state(*, started_at: datetime, config: AiIntakeConfig) -> datetime:
    return started_at + timedelta(minutes=max(config.escalate_after_minutes, 0))


def _serialize_timestamp(value: datetime | None) -> str | None:
    if not value:
        return None
    return value.astimezone(UTC).isoformat()


def _build_prompt(
    *,
    conversation: Conversation,
    history: list[Message],
    config: AiIntakeConfig,
    mappings: list[AiIntakeDepartmentMapping],
    state: dict[str, Any],
) -> tuple[str, str]:
    department_lines = []
    for mapping in mappings:
        tags = ", ".join(mapping.tags or [])
        hint = AI_INTAKE_DEPARTMENT_HINTS.get(mapping.key, "No extra routing hint.")
        department_lines.append(f"- key={mapping.key}; label={mapping.label}; tags={tags or 'none'}; intent={hint}")
    transcript = []
    for item in history:
        role = "customer" if item.direction == MessageDirection.inbound else "assistant"
        transcript.append(f"{role}: {item.body or ''}")
    instructions = (config.instructions or "").strip()
    followup_policy = (
        "You may ask one concise follow-up question when intent remains unclear."
        if config.allow_followup_questions and config.max_clarification_turns > 0
        else "Do not ask follow-up questions. If intent is unclear, return needs_followup=false and department=null."
    )
    system = (
        "You manage conversational CRM intake for inbound conversations.\n"
        "Read the full transcript and decide the most precise configured intent bucket for this customer.\n"
        "Prefer a billing subtype over generic billing when the transcript clearly fits payment, renewal, reactivation, or adjustment.\n"
        f"{followup_policy}\n"
        "Return strict JSON only with keys: department, confidence, reason, needs_followup, followup_question.\n"
        "department must be one of the configured keys or null.\n"
        "confidence must be a number from 0 to 1.\n"
        "followup_question must be empty when needs_followup is false."
    )
    prompt = (
        f"Configured departments:\n{chr(10).join(department_lines)}\n\n"
        f"Additional instructions:\n{instructions or 'None'}\n\n"
        f"Current intake state:\n"
        f"- current_status: {state.get('status') or 'new'}\n"
        f"- turns_used: {int(state.get('turn_count') or 0)}\n"
        f"- escalation_deadline: {state.get('escalate_at') or 'not_set'}\n\n"
        f"Conversation status: {conversation.status.value if conversation.status else 'unknown'}\n"
        f"Transcript:\n{chr(10).join(transcript)}"
    )
    return system, prompt


def _parse_ai_response(content: str) -> dict[str, Any]:
    stripped = content.strip()
    if not stripped:
        raise ValueError("Empty AI response")
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("AI response is not JSON")
    parsed = json.loads(stripped[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("AI response is not an object")
    return parsed


def _log_preview(value: str | None, *, limit: int = 160) -> str:
    if not value:
        return ""
    normalized = value.replace("\n", "\\n")
    return normalized[:limit]


def _handoff_team_label_for_department(department: str) -> str | None:
    labels = {
        "billing": "billing team",
        "billing_payment": "billing team",
        "billing_renewal": "billing team",
        "billing_reactivation": "billing team",
        "billing_adjustment": "billing team",
        "billing_general": "billing team",
        "support": "support team",
        "sales": "sales team",
    }
    return labels.get(department)


def _handoff_message_for_department(department: str) -> str | None:
    team_label = _handoff_team_label_for_department(department)
    if not team_label:
        return None
    return (
        f"Thanks for reaching out to us. A member of our {team_label} will "
        "respond to you shortly. Please wait for the next available agent."
    )


def _handoff_reassurance_message_for_department(department: str) -> str | None:
    team_label = _handoff_team_label_for_department(department)
    if not team_label:
        return None
    return f"Thanks for your patience - our {team_label} is still reviewing your request and will respond as soon as possible."


def _profile_update_prompt_message() -> str:
    return (
        "Thanks for reaching out to us. In the meantime, please take a moment to update your profile details so we can serve you better.\n\n"
        "You can update your information here: https://selfcare.dotmac.io/portal/profile\n\n"
        "Please remember to save your changes when you're done."
    )


def _apply_mapping_metadata(db: Session, conversation: Conversation, mapping: AiIntakeDepartmentMapping) -> None:
    logger.info(
        "ai_intake_apply_mapping conversation_id=%s department=%s team_id=%s tags=%s priority=%s",
        conversation.id,
        mapping.key,
        mapping.team_id,
        mapping.tags or [],
        mapping.priority.value if mapping.priority else None,
    )
    if mapping.priority:
        conversation.priority = mapping.priority
    if mapping.tags:
        for tag_name in mapping.tags:
            clean = tag_name.strip()
            if not clean:
                continue
            existing = (
                db.query(ConversationTag)
                .filter(ConversationTag.conversation_id == conversation.id)
                .filter(ConversationTag.tag == clean)
                .first()
            )
            if not existing:
                db.add(ConversationTag(conversation_id=conversation.id, tag=clean))


AI_INTAKE_DEPARTMENT_ALIASES = {
    "billingpayment": "billing_payment",
    "billingpayments": "billing_payment",
    "payment": "billing_payment",
    "payments": "billing_payment",
    "billingrenewal": "billing_renewal",
    "renewal": "billing_renewal",
    "billingreactivation": "billing_reactivation",
    "reactivation": "billing_reactivation",
    "billingadjustment": "billing_adjustment",
    "adjustment": "billing_adjustment",
    "billinggeneral": "billing_general",
    "generalbilling": "billing_general",
    "technicalsupport": "support",
    "techsupport": "support",
    "customersupport": "support",
    "helpdesk": "support",
}


def _normalize_department_key(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None
    canonical = raw.replace("-", "_").replace(" ", "_")
    canonical = "".join(ch for ch in canonical if ch.isalnum() or ch == "_")
    if not canonical:
        return None
    if canonical in AI_INTAKE_ALLOWED_DEPARTMENTS:
        return canonical
    alias = AI_INTAKE_DEPARTMENT_ALIASES.get(canonical.replace("_", ""))
    return alias or canonical


def _coerce_ai_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _select_department_assignment(
    db: Session,
    *,
    team_id,
) -> DepartmentRoutingSelection:
    team_uuid = coerce_uuid(team_id)
    team = db.get(CrmTeam, team_uuid)
    if not team or not team.is_active:
        logger.info(
            "ai_intake_assignment_unavailable team_id=%s strategy=least_loaded reason=team_missing_or_inactive",
            team_id,
        )
        return DepartmentRoutingSelection(
            team_id=team_uuid,
            agent_id=None,
            configured_agent_ids=(),
            active_agent_ids=(),
            reason="team_missing_or_inactive",
            routing_state="pending_department_assignment",
        )

    team_id_str = str(team_uuid)
    configured_members = (
        db.query(CrmAgent.id)
        .join(CrmAgentTeam, CrmAgentTeam.agent_id == CrmAgent.id)
        .filter(CrmAgentTeam.team_id == team_uuid)
        .filter(CrmAgentTeam.is_active.is_(True))
        .filter(CrmAgent.is_active.is_(True))
        .order_by(CrmAgent.created_at.asc(), CrmAgent.id.asc())
        .all()
    )
    configured_agent_ids = [str(row[0]) for row in configured_members]
    if not configured_agent_ids:
        logger.info(
            "ai_intake_assignment_unavailable team_id=%s strategy=least_loaded reason=no_team_members candidates=[]",
            team_id_str,
        )
        return DepartmentRoutingSelection(
            team_id=team_uuid,
            agent_id=None,
            configured_agent_ids=(),
            active_agent_ids=(),
            reason="no_team_members",
            routing_state="waiting_for_agent",
        )

    active_candidates = inbox_routing._list_active_agents(db, team_id_str)
    active_candidate_ids = [str(agent.id) for agent in active_candidates]
    if not active_candidate_ids:
        logger.info(
            "ai_intake_assignment_unavailable team_id=%s strategy=least_loaded reason=no_assignable_agents configured_candidates=%s active_candidates=[]",
            team_id_str,
            configured_agent_ids,
        )
        return DepartmentRoutingSelection(
            team_id=team_uuid,
            agent_id=None,
            configured_agent_ids=tuple(configured_agent_ids),
            active_agent_ids=(),
            reason="no_eligible_agents",
            routing_state="waiting_for_agent",
        )

    agent_id = inbox_routing._pick_least_loaded_agent(db, team_id_str)
    logger.info(
        "ai_intake_assignment_candidates team_id=%s strategy=least_loaded configured_candidates=%s active_candidates=%s chosen_agent_id=%s",
        team_id_str,
        configured_agent_ids,
        active_candidate_ids,
        agent_id,
    )
    return DepartmentRoutingSelection(
        team_id=team_uuid,
        agent_id=coerce_uuid(agent_id) if agent_id else None,
        configured_agent_ids=tuple(configured_agent_ids),
        active_agent_ids=tuple(active_candidate_ids),
        # Online agents exist (early-returned otherwise), so a None pick means they
        # are all at their concurrency cap.
        reason="assigned" if agent_id else "all_at_capacity",
        routing_state="assigned" if agent_id else "waiting_for_agent",
    )


def _set_routing_state(
    state: dict[str, Any],
    *,
    department: str | None,
    selected_team_id: uuid.UUID | None,
    assigned_team_id: uuid.UUID | None,
    assigned_agent_id: uuid.UUID | None,
    routing_state: str,
    skipped_reason: str | None = None,
    fallback_blocked: bool = False,
) -> None:
    state["routing_state"] = routing_state
    state["routing_department"] = department
    state["routing_selected_team_id"] = str(selected_team_id) if selected_team_id else None
    state["routing_assigned_team_id"] = str(assigned_team_id) if assigned_team_id else None
    state["routing_assigned_agent_id"] = str(assigned_agent_id) if assigned_agent_id else None
    state["routing_assignment_skipped_reason"] = skipped_reason
    state["routing_department_preserved"] = bool(department)
    state["routing_fallback_blocked"] = fallback_blocked


def _apply_department_assignment(
    db: Session,
    *,
    conversation: Conversation,
    mapping: AiIntakeDepartmentMapping,
    state: dict[str, Any],
    source: str,
    fallback_team_id: uuid.UUID | None = None,
    block_fallback: bool = False,
) -> DepartmentRoutingSelection:
    _apply_mapping_metadata(db, conversation, mapping)
    selection = _select_department_assignment(db, team_id=mapping.team_id)
    logger.info(
        "routing_department_selected conversation_id=%s source=%s department=%s team_id=%s fallback_team_id=%s",
        conversation.id,
        source,
        mapping.key,
        mapping.team_id,
        fallback_team_id,
    )

    assigned_team_id = (
        selection.team_id if selection.reason not in {"team_missing_or_inactive", "no_team_members"} else None
    )
    if selection.reason != "assigned":
        logger.info(
            "routing_no_eligible_agents conversation_id=%s source=%s department=%s team_id=%s reason=%s configured_candidates=%s active_candidates=%s",
            conversation.id,
            source,
            mapping.key,
            mapping.team_id,
            selection.reason,
            list(selection.configured_agent_ids),
            list(selection.active_agent_ids),
        )
        if block_fallback and fallback_team_id is not None and fallback_team_id != mapping.team_id:
            logger.info(
                "routing_fallback_blocked conversation_id=%s source=%s department=%s selected_team_id=%s fallback_team_id=%s reason=department_integrity_preserved",
                conversation.id,
                source,
                mapping.key,
                mapping.team_id,
                fallback_team_id,
            )
        logger.info(
            "routing_assignment_skipped_reason conversation_id=%s source=%s department=%s reason=%s",
            conversation.id,
            source,
            mapping.key,
            selection.reason,
        )

    conversation_service.assign_conversation(
        db,
        conversation_id=str(conversation.id),
        agent_id=str(selection.agent_id) if selection.agent_id else None,
        team_id=str(assigned_team_id) if assigned_team_id else None,
        assigned_by_id=None,
        update_lead_owner=False,
    )
    _set_routing_state(
        state,
        department=mapping.key,
        selected_team_id=mapping.team_id,
        assigned_team_id=assigned_team_id,
        assigned_agent_id=selection.agent_id,
        routing_state=selection.routing_state,
        skipped_reason=None if selection.reason == "assigned" else selection.reason,
        fallback_blocked=bool(block_fallback and fallback_team_id is not None and fallback_team_id != mapping.team_id),
    )
    logger.info(
        "routing_department_preserved conversation_id=%s source=%s department=%s routing_state=%s assigned_team_id=%s assigned_agent_id=%s",
        conversation.id,
        source,
        mapping.key,
        selection.routing_state,
        assigned_team_id,
        selection.agent_id,
    )
    return selection


def _send_followup(
    db: Session,
    *,
    conversation: Conversation,
    message: Message,
    body: str,
    message_kind: str = AI_INTAKE_FOLLOWUP_QUESTION_KIND,
    extra_metadata: dict[str, Any] | None = None,
) -> None:
    metadata = {
        "ai_intake_generated": True,
        "ai_intake_message_kind": message_kind,
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    outbound = send_message(
        db,
        InboxSendRequest(
            conversation_id=conversation.id,
            channel_type=message.channel_type,
            channel_target_id=message.channel_target_id,
            body=body,
            metadata=metadata,
        ),
        author_id=None,
        trace_id="ai-intake",
    )
    persisted_metadata = dict(outbound.metadata_ or {}) if isinstance(outbound.metadata_, dict) else {}
    persisted_metadata.update(metadata)
    outbound.metadata_ = persisted_metadata
    db.commit()


def _is_claim_stale(value: Any, *, now: datetime) -> bool:
    claimed_at = _parse_timestamp(value)
    if claimed_at is None:
        return True
    return (now - claimed_at).total_seconds() >= AI_INTAKE_SEND_CLAIM_TTL_SECONDS


def _find_existing_ai_message(
    db: Session,
    *,
    conversation_id,
    message_kind: str,
) -> Message | None:
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .filter(Message.direction == MessageDirection.outbound)
        .order_by(func.coalesce(Message.sent_at, Message.created_at).desc())
        .limit(50)
        .all()
    )
    for row in rows:
        metadata = row.metadata_ if isinstance(row.metadata_, dict) else {}
        if not metadata.get("ai_intake_generated"):
            continue
        if str(metadata.get("ai_intake_message_kind") or "").strip() != message_kind:
            continue
        return row
    return None


def _clear_handoff_send_claim(state: dict[str, Any]) -> None:
    state.pop("handoff_send_claimed_at", None)
    state.pop("handoff_send_claim_token", None)


def _clear_handoff_followup_claim(state: dict[str, Any]) -> None:
    state.pop("handoff_followup_claimed_at", None)
    state.pop("handoff_followup_claim_token", None)


def _finalize_handoff_state(
    *,
    conversation: Conversation,
    state: dict[str, Any],
    body: str,
    department: str,
    sent_at: datetime,
) -> None:
    state[AI_INTAKE_HANDOFF_SENT_KEY] = True
    state["handoff_message"] = body
    state["handoff_department"] = department
    state["handoff_state"] = AI_INTAKE_HANDOFF_STATE_AWAITING_AGENT
    state["handoff_sent_at"] = _serialize_timestamp(sent_at)
    state["handoff_followup_due_at"] = _serialize_timestamp(
        sent_at + timedelta(minutes=AI_INTAKE_HANDOFF_FOLLOWUP_MINUTES)
    )
    state["handoff_followup_sent_at"] = state.get("handoff_followup_sent_at")
    state["handoff_followup_message"] = state.get("handoff_followup_message")
    state["first_human_reply_at"] = state.get("first_human_reply_at")
    _clear_handoff_send_claim(state)
    _set_state(conversation, state)


def _send_handoff_message(
    db: Session,
    *,
    conversation: Conversation,
    message: Message,
    department: str,
) -> bool:
    body = _handoff_message_for_department(department)
    if not body:
        return False
    conversation_id = coerce_uuid(str(conversation.id))
    locked = db.query(Conversation).filter(Conversation.id == conversation_id).with_for_update(skip_locked=True).first()
    if not locked:
        return False
    state = _state(locked)
    if state.get(AI_INTAKE_HANDOFF_SENT_KEY):
        return False
    existing = _find_existing_ai_message(
        db,
        conversation_id=locked.id,
        message_kind=AI_INTAKE_HANDOFF_MESSAGE_KIND,
    )
    if existing:
        sent_at = _message_timestamp(existing) or _now()
        _finalize_handoff_state(
            conversation=locked,
            state=state,
            body=existing.body or body,
            department=department,
            sent_at=sent_at,
        )
        db.commit()
        return False

    now = _now()
    if state.get("handoff_send_claimed_at") and not _is_claim_stale(state.get("handoff_send_claimed_at"), now=now):
        logger.info(
            "ai_intake_handoff_send_suppressed conversation_id=%s reason=claim_in_progress claimed_at=%s",
            locked.id,
            state.get("handoff_send_claimed_at"),
        )
        return False

    claim_token = uuid.uuid4().hex
    state["handoff_send_claimed_at"] = _serialize_timestamp(now)
    state["handoff_send_claim_token"] = claim_token
    _set_state(locked, state)
    db.commit()

    try:
        _send_followup(
            db,
            conversation=locked,
            message=message,
            body=body,
            message_kind=AI_INTAKE_HANDOFF_MESSAGE_KIND,
            extra_metadata={"ai_intake_claim_token": claim_token},
        )
        locked = (
            db.query(Conversation).filter(Conversation.id == conversation_id).with_for_update(skip_locked=True).first()
        )
        if not locked:
            return True
        latest_state = _state(locked)
        existing = _find_existing_ai_message(
            db,
            conversation_id=locked.id,
            message_kind=AI_INTAKE_HANDOFF_MESSAGE_KIND,
        )
        sent_at = (_message_timestamp(existing) if existing else None) or now
        _finalize_handoff_state(
            conversation=locked,
            state=latest_state,
            body=(existing.body if existing and existing.body else body),
            department=department,
            sent_at=sent_at,
        )
        db.commit()
        return True
    except Exception:
        if not db.is_active:
            db.rollback()
        locked = (
            db.query(Conversation).filter(Conversation.id == conversation_id).with_for_update(skip_locked=True).first()
        )
        if locked:
            latest_state = _state(locked)
            existing = _find_existing_ai_message(
                db,
                conversation_id=locked.id,
                message_kind=AI_INTAKE_HANDOFF_MESSAGE_KIND,
            )
            if existing:
                sent_at = _message_timestamp(existing) or now
                _finalize_handoff_state(
                    conversation=locked,
                    state=latest_state,
                    body=(existing.body if existing.body else body),
                    department=department,
                    sent_at=sent_at,
                )
            elif latest_state.get("handoff_send_claim_token") == claim_token:
                _clear_handoff_send_claim(latest_state)
                _set_state(locked, latest_state)
            db.commit()
        raise


def _send_profile_update_prompt(
    db: Session,
    *,
    conversation: Conversation,
    message: Message,
) -> bool:
    body = _profile_update_prompt_message()
    conversation_id = coerce_uuid(str(conversation.id))
    locked = (
        db.query(Conversation)
        .filter(Conversation.id == conversation_id)
        .with_for_update(skip_locked=True)
        .first()
    )
    if not locked:
        return False
    existing = _find_existing_ai_message(
        db,
        conversation_id=locked.id,
        message_kind=AI_INTAKE_PROFILE_UPDATE_PROMPT_KIND,
    )
    if existing:
        return False

    _send_followup(
        db,
        conversation=locked,
        message=message,
        body=body,
        message_kind=AI_INTAKE_PROFILE_UPDATE_PROMPT_KIND,
    )
    return True


def _is_missing_profile_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, Gender):
        return value == Gender.unknown
    return False


def _profile_missing_fields(person: Person) -> tuple[list[str], list[str]]:
    standard_fields = [
        "first_name",
        "last_name",
        "display_name",
        "email",
        "phone",
        "date_of_birth",
        "gender",
        "nin",
        "address_line1",
        "address_line2",
        "city",
        "region",
        "postal_code",
        "country_code",
    ]
    missing_standard = [field for field in standard_fields if _is_missing_profile_value(getattr(person, field, None))]
    required = [field for field in ("date_of_birth", "gender") if field in missing_standard]
    return missing_standard, required


def _profile_field_label(field: str) -> str:
    return "date of birth" if field == "date_of_birth" else field.replace("_", " ")


def _profile_format_lines(missing_fields: Sequence[str]) -> list[str]:
    lines: list[str] = []
    if "date_of_birth" in missing_fields:
        lines.append("Date of birth: YYYY-MM-DD")
    if "gender" in missing_fields:
        lines.append("Gender: Male/Female/Non-binary/Other")
    return lines


def _build_profile_collection_message(*, department: str, missing_fields: Sequence[str]) -> str:
    team_label = _handoff_team_label_for_department(department) or "team"
    missing_labels = ", ".join(_profile_field_label(field) for field in missing_fields)
    format_lines = "\n".join(_profile_format_lines(missing_fields))
    return (
        f"Thanks for reaching out. A member of our {team_label} will respond shortly.\n\n"
        "In line with NCC profile requirements, we need to update your profile before we connect you. "
        f"We are missing: {missing_labels}.\n\n"
        "Please reply using this exact format:\n\n"
        f"{format_lines}"
    )


def _build_profile_retry_message(missing_fields: Sequence[str]) -> str:
    format_lines = "\n".join(_profile_format_lines(missing_fields))
    return f"Please reply using this exact format so we can update your profile:\n\n{format_lines}"


def _profile_completion_message_for_department(department: str) -> str:
    team_label = _handoff_team_label_for_department(department) or "team"
    return f"Thanks, your profile has been updated. A member of our {team_label} will be with you shortly."


def _profile_nudge_message_for_department(department: str | None) -> str:
    team_label = _handoff_team_label_for_department(department or "") or "team"
    return (
        "Please provide your profile details in the requested format so we can route your matter "
        f"to the right {team_label}."
    )


def _values_from_labeled_profile_lines(lines: Sequence[str]) -> tuple[dict[str, str], str | None]:
    values: dict[str, str] = {}
    for line in lines:
        if ":" not in line:
            return {}, "invalid_format"
        label, value = line.split(":", 1)
        clean_label = " ".join(label.strip().lower().split())
        clean_value = value.strip()
        if clean_label == "date of birth":
            values["date_of_birth"] = clean_value
        elif clean_label == "gender":
            values["gender"] = clean_value
        else:
            return {}, "unexpected_field"
    return values, None


def _values_from_bare_profile_lines(
    lines: Sequence[str], requested_fields: Sequence[str]
) -> tuple[dict[str, str], str | None]:
    if set(requested_fields) != {"date_of_birth", "gender"}:
        return {}, "invalid_format"
    if len(lines) != 2:
        return {}, "invalid_format"
    raw_dob, raw_gender = lines
    if not _PROFILE_DATE_RE.fullmatch(raw_dob):
        return {}, "invalid_date_of_birth"
    if raw_gender.strip().lower() not in _PROFILE_GENDER_VALUES:
        return {}, "invalid_gender"
    return {"date_of_birth": raw_dob, "gender": raw_gender}, None


def _parse_profile_reply(body: str | None, requested_fields: Sequence[str]) -> tuple[dict[str, Any], str | None]:
    parsed: dict[str, Any] = {}
    lines = [line.strip() for line in (body or "").strip().splitlines() if line.strip()]
    if not lines:
        return {}, "invalid_format"

    if all(":" in line for line in lines):
        values, error = _values_from_labeled_profile_lines(lines)
    elif all(":" not in line for line in lines):
        values, error = _values_from_bare_profile_lines(lines, requested_fields)
    else:
        return {}, "invalid_format"
    if error is not None:
        return {}, error

    if "date_of_birth" in requested_fields:
        raw_dob = values.get("date_of_birth")
        if not raw_dob or not _PROFILE_DATE_RE.fullmatch(raw_dob):
            return {}, "invalid_date_of_birth"
        try:
            dob = date.fromisoformat(raw_dob)
        except ValueError:
            return {}, "invalid_date_of_birth"
        if dob > _now().date():
            return {}, "future_date_of_birth"
        parsed["date_of_birth"] = dob

    if "gender" in requested_fields:
        raw_gender = values.get("gender")
        gender = _PROFILE_GENDER_VALUES.get((raw_gender or "").strip().lower())
        if gender is None:
            return {}, "invalid_gender"
        parsed["gender"] = gender

    unexpected_values = set(values) - set(requested_fields)
    if unexpected_values:
        return {}, "unexpected_field"
    return parsed, None


def _send_profile_completion_message(
    db: Session,
    *,
    conversation: Conversation,
    message: Message,
    state: dict[str, Any],
    department: str,
) -> bool:
    body = _profile_completion_message_for_department(department)
    _send_followup(
        db,
        conversation=conversation,
        message=message,
        body=body,
        message_kind=AI_INTAKE_HANDOFF_MESSAGE_KIND,
    )
    state[AI_INTAKE_HANDOFF_SENT_KEY] = True
    state["handoff_message"] = body
    state["handoff_department"] = department
    state["handoff_sent_at"] = _serialize_timestamp(_now())
    return True


def _apply_profile_update_and_sync(db: Session, *, person: Person, parsed_fields: dict[str, Any]) -> list[str]:
    updated_fields: list[str] = []
    for field_name, value in parsed_fields.items():
        if getattr(person, field_name, None) != value:
            setattr(person, field_name, value)
            updated_fields.append(field_name)
    if not updated_fields:
        return []
    db.commit()
    db.refresh(person)
    try:
        from app.services.events.handlers.selfcare_customer import enqueue_person_contact_resync

        enqueue_person_contact_resync(db, str(person.id), set(updated_fields))
    except Exception as exc:
        logger.warning("ai_intake_profile_selfcare_resync_failed person_id=%s error=%s", person.id, exc)
    return updated_fields


def _finalize_confident_match_handoff(
    db: Session,
    *,
    conversation: Conversation,
    message: Message,
    config: AiIntakeConfig,
    state: dict[str, Any],
    mapping: AiIntakeDepartmentMapping,
    source: str = "ai_intake_resolution",
) -> AiIntakeResult:
    _apply_department_assignment(
        db,
        conversation=conversation,
        mapping=mapping,
        state=state,
        source=source,
        fallback_team_id=coerce_uuid(config.fallback_team_id) if config.fallback_team_id else None,
        block_fallback=True,
    )
    now = _now()
    conversation.status = ConversationStatus.open
    state["status"] = "resolved"
    state["handoff_state"] = AI_INTAKE_HANDOFF_STATE_AWAITING_AGENT
    state["resolved_at"] = _serialize_timestamp(now)
    _set_state(conversation, state)
    db.commit()
    conversation_id_str = str(conversation.id)
    message_id_str = str(message.id)
    channel_value = message.channel_type.value if message.channel_type else "unknown"
    try:
        if state.get("profile_collection_completed"):
            _send_profile_completion_message(
                db,
                conversation=conversation,
                message=message,
                state=state,
                department=mapping.key,
            )
            _set_state(conversation, state)
            db.commit()
        else:
            handoff_sent = _send_handoff_message(
                db,
                conversation=conversation,
                message=message,
                department=mapping.key,
            )
            if handoff_sent:
                _send_profile_update_prompt(
                    db,
                    conversation=conversation,
                    message=message,
                )
    except Exception as exc:
        logger.warning(
            "ai_intake_post_handoff_send_failed scope_key=%s conversation_id=%s department=%s error=%s",
            config.scope_key,
            conversation_id_str,
            mapping.key,
            exc,
        )
        db.rollback()
    inbox_cache.invalidate_inbox_list()
    logger.info(
        "ai_intake_resolved conversation_id=%s message_id=%s scope_key=%s department=%s confidence=%s",
        conversation_id_str,
        message_id_str,
        state.get("scope_key") or config.scope_key,
        mapping.key,
        state.get("confidence"),
    )
    observe_ai_intake_result(
        outcome="resolved",
        channel=channel_value,
    )
    return AiIntakeResult(handled=True, resolved=True)


def _begin_profile_collection(
    db: Session,
    *,
    conversation: Conversation,
    message: Message,
    state: dict[str, Any],
    department: str,
    missing_standard_fields: list[str],
    required_missing_fields: list[str],
) -> AiIntakeResult:
    now = _now()
    body = _build_profile_collection_message(department=department, missing_fields=required_missing_fields)
    state["status"] = AI_INTAKE_PROFILE_STATUS
    state["handoff_state"] = AI_INTAKE_HANDOFF_STATE_NONE
    state[AI_INTAKE_HANDOFF_SENT_KEY] = True
    state["handoff_message"] = body
    state["handoff_department"] = department
    state["handoff_sent_at"] = _serialize_timestamp(now)
    state["handoff_followup_due_at"] = None
    state["profile_collection"] = {
        "requested_fields": required_missing_fields,
        "missing_standard_fields": missing_standard_fields,
        "attempt_count": 0,
        "requested_at": _serialize_timestamp(now),
        "department": department,
    }
    _set_state(conversation, state)
    conversation.status = ConversationStatus.pending
    db.commit()
    _send_followup(
        db,
        conversation=conversation,
        message=message,
        body=body,
        message_kind=AI_INTAKE_PROFILE_REQUEST_KIND,
    )
    inbox_cache.invalidate_inbox_list()
    logger.info(
        "ai_intake_profile_collection_requested conversation_id=%s message_id=%s department=%s fields=%s",
        conversation.id,
        message.id,
        department,
        required_missing_fields,
    )
    return AiIntakeResult(handled=True, waiting_for_customer=True)


def _handle_profile_collection_reply(
    db: Session,
    *,
    conversation: Conversation,
    message: Message,
    config: AiIntakeConfig,
    current_state: dict[str, Any],
) -> AiIntakeResult:
    profile_state = current_state.get("profile_collection")
    profile_state = profile_state if isinstance(profile_state, dict) else {}
    requested_fields = [
        str(field)
        for field in (profile_state.get("requested_fields") or [])
        if str(field) in {"date_of_birth", "gender"}
    ]
    department = _normalize_department_key(profile_state.get("department") or current_state.get("department"))
    mapping_by_key = {item.key: item for item in _mapping_objects(config)}
    mapping = mapping_by_key.get(department) if department else None
    if not requested_fields or not mapping:
        logger.warning(
            "ai_intake_profile_state_invalid conversation_id=%s message_id=%s department=%s requested_fields=%s",
            conversation.id,
            message.id,
            department,
            requested_fields,
        )
        if mapping:
            return _finalize_confident_match_handoff(
                db,
                conversation=conversation,
                message=message,
                config=config,
                state=dict(current_state),
                mapping=mapping,
                source="ai_intake_profile_state_invalid",
            )
        return AiIntakeResult(handled=False)

    parsed_fields, error = _parse_profile_reply(message.body, requested_fields)
    if error is None:
        person = db.get(Person, conversation.person_id) if conversation.person_id else None
        state = dict(current_state)
        if person is None:
            logger.error(
                "ai_intake_profile_person_missing conversation_id=%s message_id=%s person_id=%s",
                conversation.id,
                message.id,
                conversation.person_id,
            )
            state["profile_collection_failed"] = True
            state["profile_collection_failure_reason"] = "person_missing"
            state["profile_collection_failed_at"] = _serialize_timestamp(_now())
        else:
            updated_fields = _apply_profile_update_and_sync(db, person=person, parsed_fields=parsed_fields)
            state["profile_collection_completed"] = True
            state["profile_collection_completed_at"] = _serialize_timestamp(_now())
            state["profile_collection_updated_fields"] = updated_fields
            logger.info(
                "ai_intake_profile_collection_completed conversation_id=%s person_id=%s fields=%s",
                conversation.id,
                person.id,
                updated_fields,
            )
        return _finalize_confident_match_handoff(
            db,
            conversation=conversation,
            message=message,
            config=config,
            state=state,
            mapping=mapping,
            source="ai_intake_profile_completed",
        )

    attempt_count = int(profile_state.get("attempt_count") or 0)
    state = dict(current_state)
    profile_state = dict(profile_state)
    if attempt_count < AI_INTAKE_PROFILE_MAX_INVALID_REPLIES:
        profile_state["attempt_count"] = attempt_count + 1
        profile_state["last_error"] = error
        profile_state["last_attempt_at"] = _serialize_timestamp(_now())
        state["profile_collection"] = profile_state
        _set_state(conversation, state)
        db.commit()
        _send_followup(
            db,
            conversation=conversation,
            message=message,
            body=_build_profile_retry_message(requested_fields),
            message_kind=AI_INTAKE_PROFILE_RETRY_KIND,
        )
        inbox_cache.invalidate_inbox_list()
        logger.info(
            "ai_intake_profile_collection_retry conversation_id=%s message_id=%s error=%s fields=%s",
            conversation.id,
            message.id,
            error,
            requested_fields,
        )
        return AiIntakeResult(handled=True, followup_sent=True, waiting_for_customer=True)

    state["profile_collection_failed"] = True
    state["profile_collection_failed_at"] = _serialize_timestamp(_now())
    state["profile_collection_failure_reason"] = "invalid_format_retry_exhausted"
    state["profile_collection_failed_fields"] = requested_fields
    profile_state["attempt_count"] = attempt_count + 1
    profile_state["last_error"] = error
    profile_state["last_attempt_at"] = state["profile_collection_failed_at"]
    state["profile_collection"] = profile_state
    logger.info(
        "ai_intake_profile_collection_failed conversation_id=%s message_id=%s error=%s fields=%s",
        conversation.id,
        message.id,
        error,
        requested_fields,
    )
    return _finalize_confident_match_handoff(
        db,
        conversation=conversation,
        message=message,
        config=config,
        state=state,
        mapping=mapping,
        source="ai_intake_profile_failed",
    )


def _message_timestamp(message: Message) -> datetime | None:
    timestamp = message.sent_at or message.received_at or message.created_at
    if timestamp is None:
        return None
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=UTC)
    return timestamp


def _first_human_reply_after_handoff(
    db: Session,
    *,
    conversation_id,
    handoff_sent_at: datetime,
) -> datetime | None:
    rows = (
        db.query(Message)
        .join(CrmAgent, CrmAgent.person_id == Message.author_id)
        .filter(Message.conversation_id == conversation_id)
        .filter(Message.direction == MessageDirection.outbound)
        .filter(Message.author_id.isnot(None))
        .filter(CrmAgent.is_active.is_(True))
        .order_by(func.coalesce(Message.sent_at, Message.created_at).asc())
        .all()
    )
    for row in rows:
        metadata = row.metadata_ if isinstance(row.metadata_, dict) else {}
        if metadata.get("ai_intake_generated"):
            continue
        timestamp = _message_timestamp(row)
        if timestamp is not None and timestamp >= handoff_sent_at:
            return timestamp
    return None


def mark_handoff_in_progress_for_human_reply(
    db: Session,
    *,
    conversation: Conversation,
    message: Message,
) -> bool:
    state = _state(conversation)
    if not state:
        return False
    if state.get("handoff_state") != AI_INTAKE_HANDOFF_STATE_AWAITING_AGENT:
        return False
    if not conversation.is_active or conversation.status != ConversationStatus.open:
        return False
    handoff_sent_at = _parse_timestamp(state.get("handoff_sent_at"))
    message_timestamp = _message_timestamp(message)
    if handoff_sent_at is None or message_timestamp is None or message_timestamp < handoff_sent_at:
        return False
    if _parse_timestamp(state.get("first_human_reply_at")) is not None:
        return False

    state["first_human_reply_at"] = _serialize_timestamp(message_timestamp)
    state["handoff_state"] = AI_INTAKE_HANDOFF_STATE_IN_PROGRESS
    _set_state(conversation, state)
    logger.info(
        "ai_intake_handoff_progressed conversation_id=%s message_id=%s handoff_state=%s first_human_reply_at=%s",
        conversation.id,
        message.id,
        state["handoff_state"],
        state["first_human_reply_at"],
    )
    return True


def mark_handoff_assigned_for_manual_assignment(
    db: Session,
    *,
    conversation: Conversation,
    assigned_agent_id: str | None,
    assigned_by_id: str | None,
) -> bool:
    """Move an AI-owned conversation out of AI control when a human is assigned."""
    state = _state(conversation)
    if not state:
        return False
    if (
        state.get("status") not in AI_INTAKE_PENDING_STATES
        and state.get("handoff_state") != AI_INTAKE_HANDOFF_STATE_AWAITING_AGENT
    ):
        return False

    now = _now()
    state["status"] = "human_assigned"
    state["handoff_state"] = AI_INTAKE_HANDOFF_STATE_ASSIGNED
    state["human_assigned_at"] = _serialize_timestamp(now)
    state["human_assigned_by_id"] = assigned_by_id
    state["human_assigned_agent_id"] = assigned_agent_id
    state["routing_assigned_agent_id"] = assigned_agent_id
    _set_state(conversation, state)
    logger.info(
        "ai_intake_handoff_assigned conversation_id=%s assigned_agent_id=%s assigned_by_id=%s",
        conversation.id,
        assigned_agent_id,
        assigned_by_id,
    )
    return True


def _candidate_handoff_followup_ids(db: Session, *, limit: int) -> list[str]:
    conversations = (
        db.query(Conversation.id)
        .filter(Conversation.is_active.is_(True))
        .filter(Conversation.status == ConversationStatus.open)
        .order_by(Conversation.updated_at.asc())
        .limit(limit)
        .all()
    )
    return [str(row[0]) for row in conversations]


def _candidate_profile_nudge_ids(db: Session, *, limit: int) -> list[str]:
    rows = (
        db.query(Conversation.id)
        .filter(Conversation.is_active.is_(True))
        .filter(Conversation.status == ConversationStatus.pending)
        .filter(Conversation.metadata_.isnot(None))
        .filter(Conversation.metadata_[AI_INTAKE_METADATA_KEY]["status"].as_string() == AI_INTAKE_PROFILE_STATUS)
        .order_by(Conversation.updated_at.asc())
        .limit(limit)
        .all()
    )
    return [str(row[0]) for row in rows]


def backfill_missing_handoff_states(db: Session, *, limit: int = 500) -> dict[str, Any]:
    rows = (
        db.query(Conversation)
        .filter(Conversation.metadata_.isnot(None))
        .filter(Conversation.metadata_[AI_INTAKE_METADATA_KEY]["status"].as_string() == "resolved")
        .filter(Conversation.metadata_[AI_INTAKE_METADATA_KEY]["handoff_state"].as_string().is_(None))
        .order_by(Conversation.updated_at.asc())
        .limit(limit)
        .all()
    )
    updated = 0
    for conversation in rows:
        raw_state = (
            conversation.metadata_.get(AI_INTAKE_METADATA_KEY) if isinstance(conversation.metadata_, dict) else None
        )
        if not isinstance(raw_state, dict):
            continue
        state = _with_handoff_defaults(raw_state)
        if state == raw_state:
            continue
        _set_state(conversation, state)
        updated += 1
    if updated:
        db.commit()
        inbox_cache.invalidate_inbox_list()
    return {"processed": len(rows), "updated": updated}


def send_due_profile_collection_nudges(db: Session, *, limit: int = 200) -> dict[str, Any]:
    if not _enabled_by_env():
        return {"skipped": True, "reason": "disabled"}

    now = _now()
    processed = 0
    sent = 0
    suppressed = 0
    errors: list[str] = []

    for conversation_id in _candidate_profile_nudge_ids(db, limit=limit):
        conversation_uuid = coerce_uuid(conversation_id)
        conversation = (
            db.query(Conversation)
            .filter(Conversation.id == conversation_uuid)
            .with_for_update(skip_locked=True)
            .first()
        )
        if not conversation:
            continue
        processed += 1
        state = _state(conversation)
        if state.get("status") != AI_INTAKE_PROFILE_STATUS:
            suppressed += 1
            logger.info(
                "ai_intake_profile_nudge_suppressed conversation_id=%s reason=status ai_status=%s",
                conversation.id,
                state.get("status"),
            )
            continue
        if not conversation.is_active or conversation.status != ConversationStatus.pending:
            suppressed += 1
            logger.info(
                "ai_intake_profile_nudge_suppressed conversation_id=%s reason=conversation_not_pending is_active=%s status=%s",
                conversation.id,
                conversation.is_active,
                conversation.status,
            )
            continue
        if _parse_timestamp(state.get("profile_nudge_sent_at")) is not None:
            suppressed += 1
            logger.info(
                "ai_intake_profile_nudge_suppressed conversation_id=%s reason=already_sent sent_at=%s",
                conversation.id,
                state.get("profile_nudge_sent_at"),
            )
            continue

        raw_profile_state = state.get("profile_collection")
        profile_state: dict[str, Any] = raw_profile_state if isinstance(raw_profile_state, dict) else {}
        requested_at = _parse_timestamp(profile_state.get("requested_at")) or _parse_timestamp(
            state.get("handoff_sent_at")
        )
        if not requested_at:
            suppressed += 1
            logger.info("ai_intake_profile_nudge_suppressed conversation_id=%s reason=no_requested_at", conversation.id)
            continue
        due_at = requested_at + timedelta(minutes=AI_INTAKE_PROFILE_NUDGE_MINUTES)
        if due_at > now:
            suppressed += 1
            logger.info(
                "ai_intake_profile_nudge_suppressed conversation_id=%s reason=not_due due_at=%s now=%s",
                conversation.id,
                _serialize_timestamp(due_at),
                _serialize_timestamp(now),
            )
            continue

        inbound_after_request = (
            db.query(Message.id)
            .filter(Message.conversation_id == conversation.id)
            .filter(Message.direction == MessageDirection.inbound)
            .filter(func.coalesce(Message.received_at, Message.created_at) > requested_at)
            .first()
        )
        if inbound_after_request:
            suppressed += 1
            logger.info(
                "ai_intake_profile_nudge_suppressed conversation_id=%s reason=customer_replied_after_request",
                conversation.id,
            )
            continue

        existing_nudge = _find_existing_ai_message(
            db,
            conversation_id=conversation.id,
            message_kind=AI_INTAKE_PROFILE_NUDGE_KIND,
        )
        if existing_nudge:
            state["profile_nudge_sent_at"] = _serialize_timestamp(_message_timestamp(existing_nudge) or now)
            state["profile_nudge_message"] = existing_nudge.body or state.get("profile_nudge_message")
            _set_state(conversation, state)
            db.commit()
            suppressed += 1
            logger.info(
                "ai_intake_profile_nudge_suppressed conversation_id=%s reason=already_persisted_from_message sent_at=%s",
                conversation.id,
                state["profile_nudge_sent_at"],
            )
            continue

        inbound_message = (
            db.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .filter(Message.direction == MessageDirection.inbound)
            .order_by(func.coalesce(Message.received_at, Message.created_at).desc())
            .first()
        )
        if not inbound_message:
            suppressed += 1
            logger.info(
                "ai_intake_profile_nudge_suppressed conversation_id=%s reason=no_inbound_message", conversation.id
            )
            continue

        body = _profile_nudge_message_for_department(
            str(profile_state.get("department") or state.get("handoff_department") or state.get("department") or "")
        )
        try:
            _send_followup(
                db,
                conversation=conversation,
                message=inbound_message,
                body=body,
                message_kind=AI_INTAKE_PROFILE_NUDGE_KIND,
            )
            locked = (
                db.query(Conversation)
                .filter(Conversation.id == conversation.id)
                .with_for_update(skip_locked=True)
                .first()
            )
            if not locked:
                sent += 1
                continue
            latest_state = _state(locked)
            existing_nudge = _find_existing_ai_message(
                db,
                conversation_id=locked.id,
                message_kind=AI_INTAKE_PROFILE_NUDGE_KIND,
            )
            latest_state["profile_nudge_sent_at"] = _serialize_timestamp(
                (_message_timestamp(existing_nudge) if existing_nudge else None) or now
            )
            latest_state["profile_nudge_message"] = (
                existing_nudge.body if existing_nudge and existing_nudge.body else body
            )
            _set_state(locked, latest_state)
            db.commit()
            inbox_cache.invalidate_inbox_list()
            sent += 1
            logger.info(
                "ai_intake_profile_nudge_sent conversation_id=%s due_at=%s waited_seconds=%s",
                conversation.id,
                _serialize_timestamp(due_at),
                int(max((now - requested_at).total_seconds(), 0)),
            )
        except Exception as exc:
            if not db.is_active:
                db.rollback()
            logger.exception("ai_intake_profile_nudge_failed conversation_id=%s", conversation_id)
            errors.append(f"{conversation_id}: {exc}")

    return {
        "processed": processed,
        "sent": sent,
        "suppressed": suppressed,
        "errors": errors,
    }


def send_due_handoff_reassurance_followups(db: Session, *, limit: int = 200) -> dict[str, Any]:
    if not _enabled_by_env():
        return {"skipped": True, "reason": "disabled"}

    now = _now()
    processed = 0
    sent = 0
    suppressed = 0
    errors: list[str] = []

    for conversation_id in _candidate_handoff_followup_ids(db, limit=limit):
        conversation_uuid = coerce_uuid(conversation_id)
        conversation = (
            db.query(Conversation)
            .filter(Conversation.id == conversation_uuid)
            .with_for_update(skip_locked=True)
            .first()
        )
        if not conversation:
            continue
        processed += 1
        state = _state(conversation)
        if not state:
            suppressed += 1
            logger.info("ai_intake_handoff_followup_suppressed conversation_id=%s reason=no_state", conversation.id)
            continue
        if state.get("handoff_state") != AI_INTAKE_HANDOFF_STATE_AWAITING_AGENT:
            suppressed += 1
            logger.info(
                "ai_intake_handoff_followup_suppressed conversation_id=%s reason=handoff_state handoff_state=%s ai_status=%s",
                conversation.id,
                state.get("handoff_state"),
                state.get("status"),
            )
            continue
        if not conversation.is_active or conversation.status != ConversationStatus.open:
            suppressed += 1
            logger.info(
                "ai_intake_handoff_followup_suppressed conversation_id=%s reason=conversation_inactive_or_closed is_active=%s status=%s",
                conversation.id,
                conversation.is_active,
                conversation.status,
            )
            continue
        handoff_sent_at = _parse_timestamp(state.get("handoff_sent_at"))
        if not handoff_sent_at:
            suppressed += 1
            logger.info(
                "ai_intake_handoff_followup_suppressed conversation_id=%s reason=no_handoff_timestamp",
                conversation.id,
            )
            continue
        due_at = _parse_timestamp(state.get("handoff_followup_due_at")) or (
            handoff_sent_at + timedelta(minutes=AI_INTAKE_HANDOFF_FOLLOWUP_MINUTES)
        )
        if due_at > now:
            suppressed += 1
            logger.info(
                "ai_intake_handoff_followup_suppressed conversation_id=%s reason=not_due due_at=%s now=%s",
                conversation.id,
                _serialize_timestamp(due_at),
                _serialize_timestamp(now),
            )
            continue
        if _parse_timestamp(state.get("handoff_followup_sent_at")) is not None:
            suppressed += 1
            logger.info(
                "ai_intake_handoff_followup_suppressed conversation_id=%s reason=already_sent sent_at=%s",
                conversation.id,
                state.get("handoff_followup_sent_at"),
            )
            continue
        existing_followup = _find_existing_ai_message(
            db,
            conversation_id=conversation.id,
            message_kind=AI_INTAKE_HANDOFF_REASSURANCE_KIND,
        )
        if existing_followup:
            state["handoff_followup_sent_at"] = _serialize_timestamp(_message_timestamp(existing_followup) or now)
            state["handoff_followup_message"] = existing_followup.body or state.get("handoff_followup_message")
            _clear_handoff_followup_claim(state)
            _set_state(conversation, state)
            db.commit()
            suppressed += 1
            logger.info(
                "ai_intake_handoff_followup_suppressed conversation_id=%s reason=already_persisted_from_message sent_at=%s",
                conversation.id,
                state["handoff_followup_sent_at"],
            )
            continue
        if state.get("handoff_followup_claimed_at") and not _is_claim_stale(
            state.get("handoff_followup_claimed_at"), now=now
        ):
            suppressed += 1
            logger.info(
                "ai_intake_handoff_followup_suppressed conversation_id=%s reason=claim_in_progress claimed_at=%s",
                conversation.id,
                state.get("handoff_followup_claimed_at"),
            )
            continue

        first_human_reply_at = _first_human_reply_after_handoff(
            db,
            conversation_id=conversation.id,
            handoff_sent_at=handoff_sent_at,
        )
        if first_human_reply_at is not None:
            state["first_human_reply_at"] = _serialize_timestamp(first_human_reply_at)
            state["handoff_state"] = AI_INTAKE_HANDOFF_STATE_IN_PROGRESS
            _set_state(conversation, state)
            db.commit()
            suppressed += 1
            logger.info(
                "ai_intake_handoff_followup_suppressed conversation_id=%s reason=human_reply_detected first_human_reply_at=%s",
                conversation.id,
                state["first_human_reply_at"],
            )
            continue

        department = str(state.get("handoff_department") or state.get("department") or "").strip().lower()
        body = _handoff_reassurance_message_for_department(department)
        if not body:
            suppressed += 1
            logger.info(
                "ai_intake_handoff_followup_suppressed conversation_id=%s reason=no_department_copy department=%s",
                conversation.id,
                department,
            )
            continue

        inbound_message = (
            db.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .filter(Message.direction == MessageDirection.inbound)
            .order_by(func.coalesce(Message.received_at, Message.created_at).desc())
            .first()
        )
        if not inbound_message:
            suppressed += 1
            logger.info(
                "ai_intake_handoff_followup_suppressed conversation_id=%s reason=no_inbound_message",
                conversation.id,
            )
            continue

        try:
            claim_token = uuid.uuid4().hex
            latest_state = _state(conversation)
            latest_state["handoff_followup_claimed_at"] = _serialize_timestamp(now)
            latest_state["handoff_followup_claim_token"] = claim_token
            _set_state(conversation, latest_state)
            db.commit()
            _send_followup(
                db,
                conversation=conversation,
                message=inbound_message,
                body=body,
                message_kind=AI_INTAKE_HANDOFF_REASSURANCE_KIND,
                extra_metadata={"ai_intake_claim_token": claim_token},
            )
            locked = (
                db.query(Conversation)
                .filter(Conversation.id == conversation.id)
                .with_for_update(skip_locked=True)
                .first()
            )
            if not locked:
                sent += 1
                continue
            latest_state = _state(locked)
            existing_followup = _find_existing_ai_message(
                db,
                conversation_id=locked.id,
                message_kind=AI_INTAKE_HANDOFF_REASSURANCE_KIND,
            )
            latest_state["handoff_followup_sent_at"] = _serialize_timestamp(
                (_message_timestamp(existing_followup) if existing_followup else None) or now
            )
            latest_state["handoff_followup_message"] = (
                existing_followup.body if existing_followup and existing_followup.body else body
            )
            _clear_handoff_followup_claim(latest_state)
            _set_state(locked, latest_state)
            db.commit()
            inbox_cache.invalidate_inbox_list()
            sent += 1
            logger.info(
                "ai_intake_handoff_followup_sent conversation_id=%s department=%s due_at=%s waited_seconds=%s",
                conversation.id,
                department,
                _serialize_timestamp(due_at),
                int(max((now - handoff_sent_at).total_seconds(), 0)),
            )
        except Exception as exc:
            if not db.is_active:
                db.rollback()
            locked = (
                db.query(Conversation)
                .filter(Conversation.id == conversation_uuid)
                .with_for_update(skip_locked=True)
                .first()
            )
            if locked:
                latest_state = _state(locked)
                existing_followup = _find_existing_ai_message(
                    db,
                    conversation_id=locked.id,
                    message_kind=AI_INTAKE_HANDOFF_REASSURANCE_KIND,
                )
                if existing_followup:
                    latest_state["handoff_followup_sent_at"] = _serialize_timestamp(
                        _message_timestamp(existing_followup) or now
                    )
                    latest_state["handoff_followup_message"] = (
                        existing_followup.body
                        if existing_followup and existing_followup.body
                        else latest_state.get("handoff_followup_message")
                    )
                else:
                    _clear_handoff_followup_claim(latest_state)
                _set_state(locked, latest_state)
                db.commit()
            logger.exception("ai_intake_handoff_followup_failed conversation_id=%s", conversation_id)
            errors.append(f"{conversation_id}: {exc}")

    return {
        "processed": processed,
        "sent": sent,
        "suppressed": suppressed,
        "errors": errors,
    }


def _eligible_channel(message: Message) -> bool:
    return bool(message.channel_type in SUPPORTED_CHANNELS and message.direction == MessageDirection.inbound)


def _is_new_conversation(db: Session, conversation: Conversation, message: Message) -> bool:
    count = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id)
        .filter(Message.direction == MessageDirection.inbound)
        .count()
    )
    return count <= 1 and message.direction == MessageDirection.inbound


def _base_state(
    *,
    conversation: Conversation,
    config: AiIntakeConfig,
    scope_key: str,
    current_state: dict[str, Any],
    department: str | None = None,
    confidence: float | None = None,
    reason: str | None = None,
    endpoint: str | None = None,
    fallback_used: bool = False,
    channel: str | None = None,
) -> tuple[dict[str, Any], datetime, datetime]:
    now = _now()
    started_at = _parse_timestamp(current_state.get("started_at")) or now
    escalate_at = _deadline_for_state(started_at=started_at, config=config)
    state = {
        "status": current_state.get("status") or "pending",
        "config_id": str(config.id),
        "scope_key": scope_key,
        "started_at": _serialize_timestamp(started_at),
        "escalate_at": _serialize_timestamp(escalate_at),
        "department": department or None,
        "confidence": confidence,
        "reason": reason or None,
        "turn_count": int(current_state.get("turn_count") or 0),
        "endpoint": endpoint,
        "fallback_used": fallback_used,
        "channel": channel or current_state.get("channel") or None,
        "updated_at": _serialize_timestamp(now),
    }
    for key in (
        "recovery_attempt_count",
        "recovery_last_attempt_at",
        "recovery_last_failure_type",
        "recovery_last_error_class",
    ):
        if key in current_state:
            state[key] = current_state.get(key)
    return state, now, escalate_at


def _apply_ai_error_details(state: dict[str, Any], exc: Exception) -> dict[str, Any]:
    enriched = dict(state)
    enriched["error_class"] = type(exc).__name__
    if not isinstance(exc, AIClientError):
        return enriched
    enriched["provider"] = exc.provider
    enriched["model"] = exc.model
    enriched["endpoint"] = exc.endpoint
    enriched["failure_type"] = exc.failure_type
    enriched["timeout_type"] = exc.timeout_type
    enriched["retry_count"] = exc.retry_count
    enriched["request_id"] = exc.request_id
    enriched["response_preview"] = exc.response_preview
    enriched["transient"] = exc.transient
    return enriched


def _is_recoverable_ai_error_state(state: dict[str, Any]) -> bool:
    if state.get("status") != "escalated" or state.get("escalated_reason") != "ai_error":
        return False
    if int(state.get("recovery_attempt_count") or 0) >= AI_INTAKE_RECOVERY_MAX_ATTEMPTS:
        return False
    if state.get("handoff_sent"):
        return False
    handoff_state = str(state.get("handoff_state") or AI_INTAKE_HANDOFF_STATE_NONE)
    if handoff_state != AI_INTAKE_HANDOFF_STATE_NONE:
        return False
    failure_type = str(state.get("failure_type") or "").strip().lower()
    if failure_type in AI_INTAKE_RECOVERABLE_FAILURE_TYPES:
        return True
    response_preview = str(state.get("response_preview") or "").lower()
    return failure_type == "http_error" and "insufficient balance" in response_preview


def _prepare_recovery_state(*, current_state: dict[str, Any], now: datetime) -> dict[str, Any]:
    state = dict(current_state)
    state["status"] = "pending"
    state["reason"] = "automatic_recovery_probe"
    state["started_at"] = _serialize_timestamp(now)
    state["escalate_at"] = None
    state["updated_at"] = _serialize_timestamp(now)
    state["recovery_attempt_count"] = int(current_state.get("recovery_attempt_count") or 0) + 1
    state["recovery_last_attempt_at"] = _serialize_timestamp(now)
    state["recovery_last_failure_type"] = current_state.get("failure_type")
    state["recovery_last_error_class"] = current_state.get("error_class")
    state.pop("escalated_reason", None)
    state.pop("escalated_at", None)
    return state


def recover_ai_error_escalations(db: Session, *, limit: int = 25) -> dict[str, Any]:
    cutoff = _now() - timedelta(hours=AI_INTAKE_RECOVERY_LOOKBACK_HOURS)
    conversations = (
        db.query(Conversation)
        .filter(Conversation.metadata_.isnot(None))
        .filter(Conversation.metadata_["ai_intake"]["status"].as_string() == "escalated")
        .filter(Conversation.metadata_["ai_intake"]["escalated_reason"].as_string() == "ai_error")
        .filter(Conversation.updated_at >= cutoff)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .all()
    )

    recovered = 0
    retried = 0
    skipped = 0
    errors: list[dict[str, Any]] = []
    for conversation in conversations:
        current_state = _state(conversation)
        if not _is_recoverable_ai_error_state(current_state):
            skipped += 1
            continue
        if not conversation.is_active:
            skipped += 1
            continue

        active_assignments = (
            db.query(ConversationAssignment)
            .filter(ConversationAssignment.conversation_id == conversation.id)
            .filter(ConversationAssignment.is_active.is_(True))
            .count()
        )
        if active_assignments:
            skipped += 1
            continue

        escalated_at = _parse_timestamp(current_state.get("escalated_at"))
        outbound_query = (
            db.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .filter(Message.direction == MessageDirection.outbound)
            .filter(Message.sent_at.isnot(None))
        )
        if escalated_at is not None:
            outbound_query = outbound_query.filter(Message.sent_at >= escalated_at)
        outbound_after_escalation = outbound_query.count()
        if outbound_after_escalation:
            skipped += 1
            continue

        message = (
            db.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .filter(Message.direction == MessageDirection.inbound)
            .order_by(func.coalesce(Message.received_at, Message.created_at).desc())
            .first()
        )
        scope_key = str(current_state.get("scope_key") or "").strip() or None
        if message is None or scope_key is None:
            skipped += 1
            continue

        retried += 1
        try:
            recovery_state = _prepare_recovery_state(current_state=current_state, now=_now())
            _set_state(conversation, recovery_state)
            result = process_pending_intake(
                db,
                conversation=conversation,
                message=message,
                scope_key=scope_key,
                is_new_conversation=False,
            )
            if result.handled and not result.escalated:
                recovered += 1
        except Exception as exc:
            db.rollback()
            errors.append({"conversation_id": str(conversation.id), "error": str(exc)})
            logger.exception("ai_intake_recovery_failed conversation_id=%s", conversation.id)

    return {
        "candidates": len(conversations),
        "retried": retried,
        "recovered": recovered,
        "skipped": skipped,
        "errors": errors,
    }


def _escalate_pending_intake(
    db: Session,
    *,
    conversation: Conversation,
    config: AiIntakeConfig | None,
    current_state: dict[str, Any],
    reason: str,
) -> AiIntakeResult:
    observe_ai_intake_escalation(reason=reason)
    observe_ai_intake_result(
        outcome="escalated",
        channel=str(current_state.get("channel") or "unknown"),
        failure_type=str(current_state.get("failure_type") or reason or "none"),
    )
    state = dict(current_state)
    department = _normalize_department_key(state.get("department"))
    mapping_by_key = {item.key: item for item in _mapping_objects(config)} if config else {}
    selected_mapping = mapping_by_key.get(department) if department else None

    if selected_mapping:
        _apply_department_assignment(
            db,
            conversation=conversation,
            mapping=selected_mapping,
            state=state,
            source=f"ai_intake_escalation:{reason}",
            fallback_team_id=coerce_uuid(config.fallback_team_id) if config and config.fallback_team_id else None,
            block_fallback=True,
        )
    elif config and config.fallback_team_id:
        fallback_mapping = AiIntakeDepartmentMapping(
            key="support",
            label="Live Agent",
            team_id=coerce_uuid(config.fallback_team_id),
            tags=None,
            priority=ConversationPriority.none,
            notify_email=None,
        )
        _apply_department_assignment(
            db,
            conversation=conversation,
            mapping=fallback_mapping,
            state=state,
            source=f"ai_intake_escalation:{reason}",
        )
    conversation.status = ConversationStatus.open
    state["status"] = "escalated"
    state["escalated_reason"] = reason
    state["escalated_at"] = _serialize_timestamp(_now())
    if config:
        state["config_id"] = str(config.id)
        state["fallback_used"] = bool(config.fallback_team_id and not selected_mapping)
    _set_state(conversation, state)
    db.commit()
    inbox_cache.invalidate_inbox_list()
    return AiIntakeResult(
        handled=True,
        fallback_used=bool(config and config.fallback_team_id and not selected_mapping),
        escalated=True,
    )


def process_pending_intake(
    db: Session,
    *,
    conversation: Conversation,
    message: Message,
    scope_key: str | None,
    is_new_conversation: bool | None = None,
) -> AiIntakeResult:
    env_enabled = _enabled_by_env()
    eligible_channel = _eligible_channel(message)
    if not env_enabled or not eligible_channel:
        logger.info(
            "ai_intake_skipped conversation_id=%s message_id=%s scope_key=%s env_enabled=%s eligible_channel=%s",
            conversation.id,
            message.id,
            scope_key,
            env_enabled,
            eligible_channel,
        )
        return AiIntakeResult(handled=False)

    config = get_config_for_scope(db, scope_key)
    if not config or not config.is_enabled:
        logger.info(
            "ai_intake_config_skipped conversation_id=%s message_id=%s scope_key=%s config_found=%s config_enabled=%s",
            conversation.id,
            message.id,
            scope_key,
            bool(config),
            bool(config and config.is_enabled),
        )
        return AiIntakeResult(handled=False)

    merged_metadata = _merge_metadata(conversation, message)
    current_state = _state(conversation)

    if current_state.get("status") == AI_INTAKE_PROFILE_STATUS:
        return _handle_profile_collection_reply(
            db,
            conversation=conversation,
            message=message,
            config=config,
            current_state=current_state,
        )

    gateway_enabled = ai_gateway.enabled(db)
    if not gateway_enabled:
        logger.info(
            "ai_intake_skipped conversation_id=%s message_id=%s scope_key=%s gateway_enabled=%s",
            conversation.id,
            message.id,
            scope_key,
            gateway_enabled,
        )
        return AiIntakeResult(handled=False)

    if config.exclude_campaign_attribution and _campaign_attribution(merged_metadata):
        state, _, _ = _base_state(
            conversation=conversation,
            config=config,
            scope_key=scope_key or config.scope_key,
            current_state=current_state,
            reason="campaign_attribution",
        )
        state["status"] = "excluded"
        _set_state(conversation, state)
        db.commit()
        logger.info(
            "ai_intake_excluded conversation_id=%s message_id=%s scope_key=%s reason=campaign_attribution",
            conversation.id,
            message.id,
            scope_key or config.scope_key,
        )
        return AiIntakeResult(handled=False, excluded=True)

    if current_state.get("status") in AI_INTAKE_TERMINAL_STATES:
        logger.info(
            "ai_intake_terminal_state_skip conversation_id=%s message_id=%s scope_key=%s status=%s",
            conversation.id,
            message.id,
            scope_key or config.scope_key,
            current_state.get("status"),
        )
        return AiIntakeResult(handled=False)

    new_conversation = (
        _is_new_conversation(db, conversation, message) if is_new_conversation is None else is_new_conversation
    )
    if not new_conversation and current_state.get("status") not in AI_INTAKE_PENDING_STATES:
        logger.info(
            "ai_intake_not_new_skip conversation_id=%s message_id=%s scope_key=%s status=%s is_new_conversation=%s",
            conversation.id,
            message.id,
            scope_key or config.scope_key,
            current_state.get("status"),
            new_conversation,
        )
        return AiIntakeResult(handled=False)

    started_at = _parse_timestamp(current_state.get("started_at"))
    if (
        current_state.get("status") != AI_INTAKE_PROFILE_STATUS
        and started_at is not None
        and _now() >= _deadline_for_state(started_at=started_at, config=config)
    ):
        return _escalate_pending_intake(
            db,
            conversation=conversation,
            config=config,
            current_state=current_state,
            reason="timeout",
        )

    if conversation.status != ConversationStatus.pending:
        conversation.status = ConversationStatus.pending

    mappings = _mapping_objects(config)
    if not mappings:
        logger.warning("ai_intake_no_mappings scope_key=%s", config.scope_key)
        return AiIntakeResult(handled=False)

    system, prompt = _build_prompt(
        conversation=conversation,
        history=_history(db, conversation),
        config=config,
        mappings=mappings,
        state=current_state,
    )
    logger.info(
        "ai_intake_prompt conversation_id=%s message_id=%s scope_key=%s config_id=%s is_new_conversation=%s system_chars=%s prompt_chars=%s prompt_preview=%s",
        conversation.id,
        message.id,
        scope_key or config.scope_key,
        config.id,
        new_conversation,
        len(system),
        len(prompt),
        _log_preview(prompt),
    )
    try:
        ai_response, meta = ai_gateway.generate_with_fallback(
            db,
            system=system,
            prompt=prompt,
            max_tokens=600,
        )
        logger.info(
            "ai_intake_raw_response conversation_id=%s message_id=%s scope_key=%s endpoint=%s fallback_used=%s response_chars=%s response_preview=%s",
            conversation.id,
            message.id,
            scope_key or config.scope_key,
            meta.get("endpoint") if isinstance(meta, dict) else None,
            bool(isinstance(meta, dict) and meta.get("fallback_used")),
            len(ai_response.content or ""),
            _log_preview(ai_response.content),
        )
        parsed = _parse_ai_response(ai_response.content)
    except (AIClientError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "ai_intake_failed scope_key=%s conversation_id=%s error_class=%s failure_type=%s timeout_type=%s provider=%s model=%s endpoint=%s retry_count=%s request_id=%s transient=%s error=%s",
            config.scope_key,
            conversation.id,
            type(exc).__name__,
            getattr(exc, "failure_type", None),
            getattr(exc, "timeout_type", None),
            getattr(exc, "provider", None),
            getattr(exc, "model", None),
            getattr(exc, "endpoint", None),
            getattr(exc, "retry_count", None),
            getattr(exc, "request_id", None),
            getattr(exc, "transient", None),
            exc,
        )
        failure_state, _, _ = _base_state(
            conversation=conversation,
            config=config,
            scope_key=scope_key or config.scope_key,
            current_state=current_state,
            reason=f"ai_error:{type(exc).__name__}",
            channel=message.channel_type.value if message.channel_type else "unknown",
        )
        failure_state = _apply_ai_error_details(failure_state, exc)
        return _escalate_pending_intake(
            db,
            conversation=conversation,
            config=config,
            current_state=failure_state,
            reason="ai_error",
        )

    raw_department = parsed.get("department")
    department = _normalize_department_key(raw_department)
    confidence = parsed.get("confidence")
    try:
        confidence_value = float(confidence) if confidence is not None else 0.0
    except (TypeError, ValueError):
        confidence_value = 0.0
    reason = str(parsed.get("reason") or "").strip()
    needs_followup = _coerce_ai_bool(parsed.get("needs_followup"))
    followup_question = str(parsed.get("followup_question") or "").strip()
    mapping_by_key = {item.key: item for item in mappings}
    selected_mapping = mapping_by_key.get(department) if department else None
    logger.info(
        "ai_intake_parsed conversation_id=%s message_id=%s scope_key=%s raw_department=%s normalized_department=%s confidence=%s needs_followup=%s selected_team_id=%s selected_tags=%s",
        conversation.id,
        message.id,
        scope_key or config.scope_key,
        raw_department,
        department,
        confidence_value,
        needs_followup,
        selected_mapping.team_id if selected_mapping else None,
        selected_mapping.tags if selected_mapping else [],
    )

    next_state, now, escalate_at = _base_state(
        conversation=conversation,
        config=config,
        scope_key=scope_key or config.scope_key,
        current_state=current_state,
        department=department,
        confidence=confidence_value,
        reason=reason,
        endpoint=meta.get("endpoint") if isinstance(meta, dict) else None,
        fallback_used=bool(isinstance(meta, dict) and meta.get("fallback_used")),
        channel=message.channel_type.value if message.channel_type else "unknown",
    )

    if department in mapping_by_key and confidence_value >= config.confidence_threshold and not needs_followup:
        person = db.get(Person, conversation.person_id) if conversation.person_id else None
        if person is None:
            logger.error(
                "ai_intake_profile_person_invariant_violation conversation_id=%s message_id=%s person_id=%s",
                conversation.id,
                message.id,
                conversation.person_id,
            )
            next_state["profile_collection_skipped"] = True
            next_state["profile_collection_skip_reason"] = "person_missing"
            return _finalize_confident_match_handoff(
                db,
                conversation=conversation,
                message=message,
                config=config,
                state=next_state,
                mapping=mapping_by_key[department],
            )

        missing_standard_fields, required_missing_fields = _profile_missing_fields(person)
        if person.party_status == PartyStatus.contact:
            next_state["profile_collection_skipped"] = True
            next_state["profile_collection_skip_reason"] = "party_status_contact"
            logger.info(
                "ai_intake_profile_collection_skipped conversation_id=%s message_id=%s person_id=%s party_status=%s",
                conversation.id,
                message.id,
                person.id,
                person.party_status.value,
            )
            return _finalize_confident_match_handoff(
                db,
                conversation=conversation,
                message=message,
                config=config,
                state=next_state,
                mapping=mapping_by_key[department],
            )
        if required_missing_fields:
            return _begin_profile_collection(
                db,
                conversation=conversation,
                message=message,
                state=next_state,
                department=department,
                missing_standard_fields=missing_standard_fields,
                required_missing_fields=required_missing_fields,
            )
        return _finalize_confident_match_handoff(
            db,
            conversation=conversation,
            message=message,
            config=config,
            state=next_state,
            mapping=mapping_by_key[department],
        )

    turn_count = int(current_state.get("turn_count") or 0)
    can_follow_up = (
        config.allow_followup_questions
        and bool(followup_question)
        and needs_followup
        and turn_count < config.max_clarification_turns
        and now < escalate_at
    )
    if can_follow_up:
        next_state["status"] = "awaiting_customer"
        next_state["turn_count"] = turn_count + 1
        next_state["followup_question"] = followup_question
        _set_state(conversation, next_state)
        db.commit()
        _send_followup(db, conversation=conversation, message=message, body=followup_question)
        inbox_cache.invalidate_inbox_list()
        logger.info(
            "ai_intake_followup conversation_id=%s message_id=%s scope_key=%s question=%s confidence=%s department=%s",
            conversation.id,
            message.id,
            scope_key or config.scope_key,
            followup_question,
            confidence_value,
            department,
        )
        observe_ai_intake_result(
            outcome="followup",
            channel=message.channel_type.value if message.channel_type else "unknown",
        )
        return AiIntakeResult(handled=True, followup_sent=True, waiting_for_customer=True)

    if now >= escalate_at or config.escalate_after_minutes == 0:
        logger.info(
            "ai_intake_escalate_now conversation_id=%s message_id=%s scope_key=%s department=%s confidence=%s",
            conversation.id,
            message.id,
            scope_key or config.scope_key,
            department,
            confidence_value,
        )
        return _escalate_pending_intake(
            db,
            conversation=conversation,
            config=config,
            current_state=next_state,
            reason="unresolved",
        )

    next_state["status"] = "awaiting_timeout"
    next_state["turn_count"] = turn_count
    next_state["followup_question"] = None
    _set_state(conversation, next_state)
    db.commit()
    logger.info(
        "ai_intake_waiting_timeout conversation_id=%s message_id=%s scope_key=%s department=%s confidence=%s",
        conversation.id,
        message.id,
        scope_key or config.scope_key,
        department,
        confidence_value,
    )
    observe_ai_intake_result(
        outcome="awaiting_timeout",
        channel=message.channel_type.value if message.channel_type else "unknown",
    )
    return AiIntakeResult(handled=True)


def escalate_expired_pending_intakes(db: Session, *, limit: int = 200) -> dict[str, Any]:
    if not _enabled_by_env():
        return {"skipped": True, "reason": "disabled"}

    now = _now()
    conversations = (
        db.query(Conversation)
        .filter(Conversation.is_active.is_(True))
        .filter(Conversation.status == ConversationStatus.pending)
        .order_by(Conversation.updated_at.asc())
        .limit(limit)
        .all()
    )

    escalated = 0
    skipped = 0
    errors: list[str] = []

    for conversation in conversations:
        state = _state(conversation)
        if not state or state.get("status") not in AI_INTAKE_PENDING_STATES:
            skipped += 1
            continue
        if state.get("status") == AI_INTAKE_PROFILE_STATUS:
            skipped += 1
            logger.info(
                "ai_intake_timeout_escalation_skipped conversation_id=%s reason=awaiting_profile",
                conversation.id,
            )
            continue
        deadline = _parse_timestamp(state.get("escalate_at"))
        if deadline is None:
            skipped += 1
            continue
        if deadline > now:
            skipped += 1
            continue
        config = get_config_for_scope(db, state.get("scope_key"))
        try:
            _escalate_pending_intake(
                db,
                conversation=conversation,
                config=config,
                current_state=state,
                reason="timeout",
            )
            escalated += 1
        except Exception as exc:
            db.rollback()
            logger.exception("ai_intake_timeout_escalation_failed conversation_id=%s", conversation.id)
            errors.append(f"{conversation.id}: {exc}")

    return {
        "processed": len(conversations),
        "escalated": escalated,
        "skipped": skipped,
        "errors": errors,
    }


def retry_team_only_ai_assignments(db: Session, *, limit: int = 200) -> dict[str, Any]:
    """Retry agent assignment for AI-routed conversations left on team queue only."""
    if not _enabled_by_env():
        return {"skipped": True, "reason": "disabled"}

    rows = (
        db.query(Conversation, ConversationAssignment)
        .join(
            ConversationAssignment,
            ConversationAssignment.conversation_id == Conversation.id,
        )
        .filter(Conversation.is_active.is_(True))
        .filter(ConversationAssignment.is_active.is_(True))
        .filter(ConversationAssignment.team_id.isnot(None))
        .filter(ConversationAssignment.agent_id.is_(None))
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .all()
    )

    retried = 0
    assigned = 0
    skipped = 0
    errors: list[str] = []

    for conversation, assignment in rows:
        state = _state(conversation)
        if state.get("status") not in {"resolved", "escalated"}:
            skipped += 1
            continue
        if not assignment.team_id:
            skipped += 1
            continue

        retried += 1
        try:
            selection = _select_department_assignment(db, team_id=assignment.team_id)
            state["last_assignment_retry_at"] = _serialize_timestamp(_now())
            state["last_assignment_retry_reason"] = selection.reason
            if not selection.agent_id:
                assigned_team_id = assignment.team_id if selection.reason != "no_team_members" else None
                if assigned_team_id is None:
                    conversation_service.assign_conversation(
                        db,
                        conversation_id=str(conversation.id),
                        agent_id=None,
                        team_id=None,
                        assigned_by_id=None,
                        update_lead_owner=False,
                    )
                _set_routing_state(
                    state,
                    department=_normalize_department_key(state.get("department")),
                    selected_team_id=assignment.team_id,
                    assigned_team_id=assigned_team_id,
                    assigned_agent_id=None,
                    routing_state="waiting_for_agent",
                    skipped_reason=selection.reason,
                    fallback_blocked=bool(state.get("routing_fallback_blocked")),
                )
                _set_state(conversation, state)
                db.commit()
                continue

            conversation_service.assign_conversation(
                db,
                conversation_id=str(conversation.id),
                agent_id=str(selection.agent_id),
                team_id=str(assignment.team_id),
                assigned_by_id=None,
                update_lead_owner=False,
            )
            state["agent_assigned_at"] = _serialize_timestamp(_now())
            state["agent_id"] = str(selection.agent_id)
            _set_routing_state(
                state,
                department=_normalize_department_key(state.get("department")),
                selected_team_id=assignment.team_id,
                assigned_team_id=assignment.team_id,
                assigned_agent_id=selection.agent_id,
                routing_state="assigned",
            )
            _set_state(conversation, state)
            db.commit()
            inbox_cache.invalidate_inbox_list()
            assigned += 1
        except Exception as exc:
            db.rollback()
            logger.exception("ai_intake_assignment_retry_failed conversation_id=%s", conversation.id)
            errors.append(f"{conversation.id}: {exc}")

    return {
        "processed": len(rows),
        "retried": retried,
        "assigned": assigned,
        "skipped": skipped,
        "errors": errors,
    }


def _handoff_action_at(state: dict[str, Any]) -> datetime | None:
    candidates = [
        _parse_timestamp(state.get("human_assigned_at")),
        _parse_timestamp(state.get("assigned_at")),
        _parse_timestamp(state.get("agent_assigned_at")),
        _parse_timestamp(state.get("escalated_at")),
        _parse_timestamp(state.get("resolved_at")),
        _parse_timestamp(state.get("handoff_sent_at")),
    ]
    parsed = [candidate for candidate in candidates if candidate is not None]
    return max(parsed) if parsed else None


def _missed_handoff_reference_at(
    *,
    state: dict[str, Any],
    assignment: ConversationAssignment,
) -> datetime | None:
    assignment_at = assignment.assigned_at
    if assignment_at is not None and assignment_at.tzinfo is None:
        assignment_at = assignment_at.replace(tzinfo=UTC)
    handoff_at = _handoff_action_at(state)
    candidates = [candidate for candidate in [assignment_at, handoff_at] if candidate is not None]
    return max(candidates) if candidates else None


def _select_reassignment_agent(
    db: Session,
    *,
    team_id: str,
    exclude_agent_id: str | None,
):
    active_agents = inbox_routing._list_active_agents(db, team_id)
    if exclude_agent_id:
        active_agents = [agent for agent in active_agents if str(agent.id) != str(exclude_agent_id)]
    if not active_agents:
        return None

    load_map = inbox_routing._agent_active_chat_counts(db, [agent.id for agent in active_agents])
    default_cap = inbox_routing._global_max_concurrent(db)
    available = [
        agent for agent in active_agents if load_map.get(agent.id, 0) < inbox_routing._agent_cap(agent, default_cap)
    ]
    if not available:
        return None
    available.sort(key=lambda agent: (load_map.get(agent.id, 0), agent.created_at, str(agent.id)))
    return available[0]


def _ai_handoff_reassignment_timeout_minutes(db: Session) -> int:
    from app.services.settings_spec import SettingDomain, resolve_value

    value = resolve_value(db, SettingDomain.notification, "crm_inbox_ai_handoff_reassign_after_minutes")
    if isinstance(value, bool):
        minutes = AI_INTAKE_HANDOFF_REASSIGN_MINUTES
    elif isinstance(value, int | float | str):
        try:
            minutes = int(value)
        except ValueError:
            minutes = AI_INTAKE_HANDOFF_REASSIGN_MINUTES
    else:
        minutes = AI_INTAKE_HANDOFF_REASSIGN_MINUTES
    return max(minutes, 1)


def reassign_stale_ai_handoffs(db: Session, *, limit: int = 200) -> dict[str, Any]:
    """Reassign AI handoffs when the first assigned agent has not replied in time."""
    timeout_minutes = _ai_handoff_reassignment_timeout_minutes(db)
    cutoff = _now() - timedelta(minutes=timeout_minutes)
    rows = (
        db.query(Conversation, ConversationAssignment)
        .join(ConversationAssignment, ConversationAssignment.conversation_id == Conversation.id)
        .filter(Conversation.is_active.is_(True))
        .filter(Conversation.status.in_([ConversationStatus.open, ConversationStatus.pending]))
        .filter(Conversation.first_response_at.is_(None))
        .filter(ConversationAssignment.is_active.is_(True))
        .filter(ConversationAssignment.agent_id.isnot(None))
        .order_by(ConversationAssignment.assigned_at.asc())
        .limit(limit)
        .all()
    )

    processed = 0
    reassigned = 0
    queued = 0
    skipped = 0
    errors: list[str] = []

    for conversation, assignment in rows:
        state = _state(conversation)
        if state.get("status") not in {"resolved", "escalated", "human_assigned"}:
            skipped += 1
            continue

        reference_at = _missed_handoff_reference_at(state=state, assignment=assignment)
        if reference_at is None or reference_at > cutoff:
            skipped += 1
            continue

        team_id = assignment.team_id or state.get("routing_assigned_team_id") or state.get("routing_selected_team_id")
        if not team_id:
            skipped += 1
            continue

        processed += 1
        try:
            next_agent = _select_reassignment_agent(
                db,
                team_id=str(team_id),
                exclude_agent_id=str(assignment.agent_id) if assignment.agent_id else None,
            )
            now = _now()
            state["missed_handoff_reassigned_at"] = _serialize_timestamp(now)
            state["missed_handoff_previous_agent_id"] = str(assignment.agent_id) if assignment.agent_id else None
            state["missed_handoff_reference_at"] = _serialize_timestamp(reference_at)
            state["missed_handoff_reassign_after_minutes"] = timeout_minutes
            state["missed_handoff_reassignment_count"] = int(state.get("missed_handoff_reassignment_count") or 0) + 1

            if next_agent is None:
                conversation_service.assign_conversation(
                    db,
                    conversation_id=str(conversation.id),
                    agent_id=None,
                    team_id=str(team_id),
                    assigned_by_id=None,
                    update_lead_owner=False,
                )
                state["missed_handoff_reassigned_agent_id"] = None
                state["missed_handoff_reassignment_result"] = "team_queue"
                _set_routing_state(
                    state,
                    department=_normalize_department_key(state.get("department")),
                    selected_team_id=team_id,
                    assigned_team_id=team_id,
                    assigned_agent_id=None,
                    routing_state="waiting_for_agent",
                    skipped_reason="missed_first_response",
                    fallback_blocked=bool(state.get("routing_fallback_blocked")),
                )
                queued += 1
            else:
                conversation_service.assign_conversation(
                    db,
                    conversation_id=str(conversation.id),
                    agent_id=str(next_agent.id),
                    team_id=str(team_id),
                    assigned_by_id=None,
                    update_lead_owner=False,
                )
                state["missed_handoff_reassigned_agent_id"] = str(next_agent.id)
                state["missed_handoff_reassignment_result"] = "reassigned"
                _set_routing_state(
                    state,
                    department=_normalize_department_key(state.get("department")),
                    selected_team_id=team_id,
                    assigned_team_id=team_id,
                    assigned_agent_id=next_agent.id,
                    routing_state="assigned",
                )
                reassigned += 1

            _set_state(conversation, state)
            db.commit()
            inbox_cache.invalidate_inbox_list()
            logger.info(
                "ai_intake_missed_handoff_reassigned conversation_id=%s previous_agent_id=%s new_agent_id=%s team_id=%s result=%s",
                conversation.id,
                assignment.agent_id,
                state.get("missed_handoff_reassigned_agent_id"),
                team_id,
                state.get("missed_handoff_reassignment_result"),
            )
        except Exception as exc:
            db.rollback()
            logger.exception("ai_intake_missed_handoff_reassign_failed conversation_id=%s", conversation.id)
            errors.append(f"{conversation.id}: {exc}")

    return {
        "processed": processed,
        "reassigned": reassigned,
        "queued": queued,
        "skipped": skipped,
        "errors": errors,
    }
