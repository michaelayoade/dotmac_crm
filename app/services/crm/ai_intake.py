from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.models.crm.ai_intake import AiIntakeConfig
from app.models.crm.conversation import Conversation, ConversationTag, Message
from app.models.crm.enums import ChannelType, ConversationPriority, ConversationStatus, MessageDirection
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
from app.services.crm.inbox.outbound import send_message

logger = logging.getLogger(__name__)

AI_INTAKE_METADATA_KEY = "ai_intake"
AI_INTAKE_PENDING_STATES = {"pending", "awaiting_customer", "awaiting_timeout"}
AI_INTAKE_TERMINAL_STATES = {"resolved", "escalated", "excluded"}
AI_INTAKE_ALLOWED_DEPARTMENTS = {"billing", "support", "sales"}
SUPPORTED_CHANNELS = {
    ChannelType.whatsapp,
    ChannelType.facebook_messenger,
    ChannelType.instagram_dm,
    ChannelType.chat_widget,
}
ENV_FLAG = "CRM_AI_PENDING_INTAKE_ENABLED"


@dataclass(frozen=True)
class AiIntakeResult:
    handled: bool
    resolved: bool = False
    followup_sent: bool = False
    excluded: bool = False
    fallback_used: bool = False
    escalated: bool = False
    waiting_for_customer: bool = False


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


def _state(conversation: Conversation) -> dict[str, Any]:
    if not isinstance(conversation.metadata_, dict):
        return {}
    current = conversation.metadata_.get(AI_INTAKE_METADATA_KEY)
    return dict(current) if isinstance(current, dict) else {}


def _set_state(conversation: Conversation, state: dict[str, Any]) -> None:
    metadata = dict(conversation.metadata_ or {}) if isinstance(conversation.metadata_, dict) else {}
    metadata[AI_INTAKE_METADATA_KEY] = state
    conversation.metadata_ = metadata


def _history(db: Session, conversation: Conversation, limit: int = 12) -> list[Message]:
    messages = (
        db.query(Message).filter(Message.conversation_id == conversation.id).order_by(Message.created_at.asc()).all()
    )
    return messages[-limit:]


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
        department_lines.append(f"- key={mapping.key}; label={mapping.label}; tags={tags or 'none'}")
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
        "Read the full transcript and decide whether the customer intent is billing, support, or sales.\n"
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


def _apply_mapping(db: Session, conversation: Conversation, mapping: AiIntakeDepartmentMapping) -> None:
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
    if mapping.team_id:
        conversation_service.assign_conversation(
            db,
            conversation_id=str(conversation.id),
            agent_id=None,
            team_id=str(mapping.team_id),
            assigned_by_id=None,
            update_lead_owner=False,
        )


def _send_followup(
    db: Session,
    *,
    conversation: Conversation,
    message: Message,
    body: str,
) -> None:
    outbound = send_message(
        db,
        InboxSendRequest(
            conversation_id=conversation.id,
            channel_type=message.channel_type,
            channel_target_id=message.channel_target_id,
            body=body,
        ),
        author_id=None,
        trace_id="ai-intake",
    )
    metadata = dict(outbound.metadata_ or {}) if isinstance(outbound.metadata_, dict) else {}
    metadata["ai_intake_generated"] = True
    outbound.metadata_ = metadata
    db.commit()


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
        "updated_at": _serialize_timestamp(now),
    }
    return state, now, escalate_at


def _escalate_pending_intake(
    db: Session,
    *,
    conversation: Conversation,
    config: AiIntakeConfig | None,
    current_state: dict[str, Any],
    reason: str,
) -> AiIntakeResult:
    if config and config.fallback_team_id:
        fallback_mapping = AiIntakeDepartmentMapping(
            key="support",
            label="Live Agent",
            team_id=coerce_uuid(config.fallback_team_id),
            tags=None,
            priority=ConversationPriority.none,
            notify_email=None,
        )
        _apply_mapping(db, conversation, fallback_mapping)
    conversation.status = ConversationStatus.open
    state = dict(current_state)
    state["status"] = "escalated"
    state["escalated_reason"] = reason
    state["escalated_at"] = _serialize_timestamp(_now())
    if config:
        state["config_id"] = str(config.id)
        state["fallback_used"] = bool(config.fallback_team_id)
    _set_state(conversation, state)
    db.commit()
    inbox_cache.invalidate_inbox_list()
    return AiIntakeResult(handled=True, fallback_used=bool(config and config.fallback_team_id), escalated=True)


def process_pending_intake(
    db: Session,
    *,
    conversation: Conversation,
    message: Message,
    scope_key: str | None,
    is_new_conversation: bool | None = None,
) -> AiIntakeResult:
    if not _enabled_by_env() or not _eligible_channel(message) or not ai_gateway.enabled(db):
        return AiIntakeResult(handled=False)

    config = get_config_for_scope(db, scope_key)
    if not config or not config.is_enabled:
        return AiIntakeResult(handled=False)

    merged_metadata = _merge_metadata(conversation, message)
    current_state = _state(conversation)

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
        return AiIntakeResult(handled=False, excluded=True)

    if current_state.get("status") in AI_INTAKE_TERMINAL_STATES:
        return AiIntakeResult(handled=False)

    new_conversation = (
        _is_new_conversation(db, conversation, message) if is_new_conversation is None else is_new_conversation
    )
    if not new_conversation and current_state.get("status") not in AI_INTAKE_PENDING_STATES:
        return AiIntakeResult(handled=False)

    started_at = _parse_timestamp(current_state.get("started_at"))
    if started_at is not None and _now() >= _deadline_for_state(started_at=started_at, config=config):
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
    try:
        ai_response, meta = ai_gateway.generate_with_fallback(
            db,
            system=system,
            prompt=prompt,
            max_tokens=600,
        )
        parsed = _parse_ai_response(ai_response.content)
    except (AIClientError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "ai_intake_failed scope_key=%s conversation_id=%s error=%s", config.scope_key, conversation.id, exc
        )
        return AiIntakeResult(handled=False)

    department = str(parsed.get("department") or "").strip().lower() or None
    confidence = parsed.get("confidence")
    try:
        confidence_value = float(confidence) if confidence is not None else 0.0
    except (TypeError, ValueError):
        confidence_value = 0.0
    reason = str(parsed.get("reason") or "").strip()
    needs_followup = bool(parsed.get("needs_followup"))
    followup_question = str(parsed.get("followup_question") or "").strip()
    mapping_by_key = {item.key: item for item in mappings}

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
    )

    if department in mapping_by_key and confidence_value >= config.confidence_threshold and not needs_followup:
        _apply_mapping(db, conversation, mapping_by_key[department])
        conversation.status = ConversationStatus.open
        next_state["status"] = "resolved"
        next_state["resolved_at"] = _serialize_timestamp(now)
        _set_state(conversation, next_state)
        db.commit()
        inbox_cache.invalidate_inbox_list()
        return AiIntakeResult(handled=True, resolved=True)

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
        return AiIntakeResult(handled=True, followup_sent=True, waiting_for_customer=True)

    if now >= escalate_at or config.escalate_after_minutes == 0:
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
