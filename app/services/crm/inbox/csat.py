"""CSAT helpers for CRM inbox."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.models.comms import (
    CustomerSurveyStatus,
    Survey,
    SurveyInvitation,
    SurveyResponse,
    SurveyTriggerType,
)
from app.models.crm.conversation import Conversation, Message
from app.models.crm.enums import ChannelType, MessageDirection
from app.models.domain_settings import SettingDomain, SettingValueType
from app.models.person import Person
from app.schemas.crm.inbox import InboxSendRequest
from app.schemas.settings import DomainSettingUpdate
from app.services import crm as crm_service
from app.services import domain_settings as domain_settings_service
from app.services import email as email_service
from app.services import settings_spec
from app.services.common import coerce_uuid
from app.services.crm.inbox.permissions import can_manage_inbox_settings
from app.services.surveys import survey_invitations

logger = logging.getLogger(__name__)

CSAT_ENABLED_BY_TARGET_KEY = "crm_inbox_csat_enabled_by_target"


@dataclass(frozen=True)
class CsatToggleResult:
    ok: bool
    error_detail: str | None = None


@dataclass(frozen=True)
class CsatQueueResult:
    kind: Literal[
        "queued",
        "not_enabled",
        "no_target",
        "no_person_email",
        "no_active_survey",
        "already_invited",
        "error",
    ]
    detail: str | None = None


@dataclass(frozen=True)
class CsatConversationEvent:
    id: str
    timestamp: object
    survey_name: str | None
    rating: int | None
    feedback: str | None


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def get_enabled_map(db: Session) -> dict[str, bool]:
    raw = settings_spec.resolve_value(db, SettingDomain.notification, CSAT_ENABLED_BY_TARGET_KEY)
    if not isinstance(raw, dict):
        return {}
    enabled: dict[str, bool] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        key = key.strip()
        if not key:
            continue
        enabled[key] = _to_bool(value)
    return enabled


def update_inbox_toggle(
    db: Session,
    *,
    target_id: str,
    enabled: bool,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> CsatToggleResult:
    try:
        if (roles is not None or scopes is not None) and not can_manage_inbox_settings(roles, scopes):
            return CsatToggleResult(ok=False, error_detail="Not authorized to update CSAT settings")
        target_key = (target_id or "").strip()
        if not target_key:
            return CsatToggleResult(ok=False, error_detail="Inbox target is required")
        settings_service = domain_settings_service.DomainSettings(SettingDomain.notification)
        updated = get_enabled_map(db)
        updated[target_key] = bool(enabled)
        settings_service.upsert_by_key(
            db,
            CSAT_ENABLED_BY_TARGET_KEY,
            DomainSettingUpdate(
                value_type=SettingValueType.json,
                value_text=None,
                value_json=updated,
            ),
        )
        return CsatToggleResult(ok=True)
    except Exception as exc:
        return CsatToggleResult(ok=False, error_detail=str(exc) or "Failed to update CSAT setting")


def _resolve_target_id(db: Session, conversation_id: str) -> str | None:
    conversation_uuid = coerce_uuid(conversation_id)
    channel_target_id = (
        db.query(Message.channel_target_id)
        .filter(Message.conversation_id == conversation_uuid)
        .filter(Message.channel_target_id.isnot(None))
        .order_by(Message.created_at.desc())
        .limit(1)
        .scalar()
    )
    if not channel_target_id:
        return None
    return str(channel_target_id)


def _resolve_latest_channel_type(db: Session, conversation_id: str) -> ChannelType | None:
    conversation_uuid = coerce_uuid(conversation_id)
    return (
        db.query(Message.channel_type)
        .filter(Message.conversation_id == conversation_uuid)
        .order_by(Message.created_at.desc())
        .limit(1)
        .scalar()
    )


def _channel_toggle_key(channel_type: ChannelType) -> str:
    return f"channel:{channel_type.value}"


def _pick_active_survey(db: Session) -> Survey | None:
    survey = (
        db.query(Survey)
        .filter(Survey.is_active.is_(True), Survey.status == CustomerSurveyStatus.active)
        .filter(Survey.trigger_type == SurveyTriggerType.manual)
        .order_by(Survey.updated_at.desc())
        .first()
    )
    if survey:
        return survey
    return (
        db.query(Survey)
        .filter(Survey.is_active.is_(True), Survey.status == CustomerSurveyStatus.active)
        .order_by(Survey.updated_at.desc())
        .first()
    )


def _resolve_survey_link(db: Session, token: str) -> str:
    base_url = email_service.get_app_url(db).strip().rstrip("/")
    return f"{base_url}/s/t/{token}"


def _resolve_latest_inbound_message(db: Session, conversation_id: str) -> Message | None:
    return (
        db.query(Message)
        .filter(Message.conversation_id == coerce_uuid(conversation_id))
        .filter(Message.direction == MessageDirection.inbound)
        .order_by(Message.created_at.desc())
        .first()
    )


def queue_for_resolved_conversation(
    db: Session,
    *,
    conversation_id: str,
    author_id: str | None = None,
) -> CsatQueueResult:
    try:
        conversation = db.get(Conversation, coerce_uuid(conversation_id))
        if not conversation:
            return CsatQueueResult(kind="error", detail="Conversation not found")
        last_inbound = _resolve_latest_inbound_message(db, conversation_id)
        target_id = _resolve_target_id(db, conversation_id)
        channel_type = last_inbound.channel_type if last_inbound and last_inbound.channel_type else None
        if channel_type is None:
            channel_type = _resolve_latest_channel_type(db, conversation_id)
        if channel_type is None:
            return CsatQueueResult(kind="no_target")
        if not target_id:
            return CsatQueueResult(kind="no_target")
        enabled_map = get_enabled_map(db)
        target_enabled = bool(target_id and enabled_map.get(target_id, False))
        channel_enabled = bool(channel_type and enabled_map.get(_channel_toggle_key(channel_type), False))
        if not target_enabled and not channel_enabled:
            return CsatQueueResult(kind="not_enabled")

        person = db.get(Person, conversation.person_id)
        if not person:
            return CsatQueueResult(kind="error", detail="Contact not found")

        survey = _pick_active_survey(db)
        if not survey:
            return CsatQueueResult(kind="no_active_survey")

        existing = (
            db.query(SurveyInvitation.id)
            .filter(SurveyInvitation.survey_id == survey.id, SurveyInvitation.person_id == person.id)
            .first()
        )
        if existing:
            return CsatQueueResult(kind="already_invited")

        invitation = survey_invitations.create_for_person(
            db,
            survey_id=str(survey.id),
            person_id=str(person.id),
            email=person.email or f"csat+{person.id}@local.invalid",
            ticket_id=str(conversation.ticket_id) if conversation.ticket_id else None,
            expires_at=survey.expires_at,
        )
        survey_url = _resolve_survey_link(db, invitation.token)
        outbound_payload = InboxSendRequest(
            conversation_id=conversation.id,
            channel_type=channel_type,
            channel_target_id=coerce_uuid(target_id) if target_id else None,
            reply_to_message_id=last_inbound.id if last_inbound else None,
            body=(f"Your conversation has been resolved. Please rate your experience here: {survey_url}"),
        )
        crm_service.inbox.send_message_with_retry(
            db,
            outbound_payload,
            author_id=author_id,
        )
        survey_invitations.mark_sent(db, invitation)
        survey.total_invited = (survey.total_invited or 0) + 1
        db.commit()
        return CsatQueueResult(kind="queued")
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to queue CSAT invitation for resolved conversation %s", conversation_id)
        return CsatQueueResult(kind="error", detail=str(exc))


def _extract_feedback_text(response_payload: object) -> str | None:
    if not isinstance(response_payload, dict):
        return None
    prioritized_keys = ("feedback", "comment", "comments", "message", "note", "notes", "text")
    for key in prioritized_keys:
        value = response_payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for value in response_payload.values():
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def get_conversation_csat_event(db: Session, *, conversation_id: str) -> CsatConversationEvent | None:
    """Return latest CSAT response reliably linked to this conversation.

    Reliability rule:
    - Conversation must be linked to a ticket.
    - Response must be submitted through an invitation for the same person + ticket.
    """
    conversation = db.get(Conversation, coerce_uuid(conversation_id))
    if not conversation or not conversation.ticket_id:
        return None

    row = (
        db.query(SurveyResponse, SurveyInvitation, Survey)
        .join(
            SurveyInvitation,
            SurveyInvitation.id == SurveyResponse.invitation_id,
        )
        .join(
            Survey,
            Survey.id == SurveyResponse.survey_id,
        )
        .filter(SurveyInvitation.ticket_id == conversation.ticket_id)
        .filter(SurveyInvitation.person_id == conversation.person_id)
        .order_by(SurveyResponse.completed_at.desc().nullslast(), SurveyResponse.created_at.desc())
        .first()
    )
    if not row:
        return None
    response, _invitation, survey = row
    feedback = _extract_feedback_text(response.responses)
    return CsatConversationEvent(
        id=str(response.id),
        timestamp=response.completed_at or response.created_at,
        survey_name=survey.name if survey else None,
        rating=response.rating,
        feedback=feedback,
    )
