"""Conversation action helpers for CRM inbox."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation
from app.models.crm.enums import ChannelType
from app.services import person as person_service
from app.services.common import coerce_uuid
from app.services.crm import contact as contact_service
from app.services.crm import conversation as conversation_service
from app.services.crm.inbox.audit import log_conversation_action
from app.services.crm.inbox.permissions import (
    can_assign_conversation,
    can_resolve_conversation,
)


@dataclass(frozen=True)
class AssignConversationResult:
    kind: Literal["forbidden", "not_found", "invalid_input", "error", "success"]
    conversation: Conversation | None = None
    contact: object | None = None
    error_detail: str | None = None


@dataclass(frozen=True)
class ResolveConversationResult:
    kind: Literal["forbidden", "not_found", "invalid_channel", "error", "success"]
    conversation: Conversation | None = None
    contact: object | None = None
    error_detail: str | None = None


def assign_conversation(
    db: Session,
    *,
    conversation_id: str,
    agent_id: str | None,
    team_id: str | None,
    assigned_by_id: str | None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
) -> AssignConversationResult:
    if (roles is not None or scopes is not None) and not can_assign_conversation(roles, scopes):
        return AssignConversationResult(
            kind="forbidden",
            error_detail="Not authorized to assign conversations",
        )
    try:
        conversation_uuid = coerce_uuid(conversation_id)
    except Exception:
        return AssignConversationResult(kind="not_found")
    conversation = db.get(Conversation, conversation_uuid)
    if not conversation:
        return AssignConversationResult(kind="not_found")

    agent_value = (agent_id or "").strip() or None
    team_value = (team_id or "").strip() or None
    try:
        if agent_value:
            agent_value = str(coerce_uuid(agent_value))
        if team_value:
            team_value = str(coerce_uuid(team_value))
    except Exception:
        return AssignConversationResult(kind="invalid_input", error_detail="Invalid agent or team selection.")

    assigned_by_value = (assigned_by_id or "").strip() or None
    try:
        if assigned_by_value:
            assigned_by_value = str(coerce_uuid(assigned_by_value))
    except Exception:
        assigned_by_value = None

    try:
        conversation_service.assign_conversation(
            db,
            conversation_id=conversation_id,
            agent_id=agent_value,
            team_id=team_value,
            assigned_by_id=assigned_by_value,
            update_lead_owner=True,
        )
    except Exception as exc:
        return AssignConversationResult(kind="error", conversation=conversation, error_detail=str(exc))

    contact = contact_service.get_person_with_relationships(db, str(conversation.contact_id))
    log_conversation_action(
        db,
        action="assign_conversation",
        conversation_id=str(conversation.id),
        actor_id=assigned_by_value,
        metadata={"agent_id": agent_value, "team_id": team_value},
    )
    return AssignConversationResult(
        kind="success",
        conversation=conversation,
        contact=contact,
    )


def resolve_conversation(
    db: Session,
    *,
    conversation_id: str,
    person_id: str,
    channel_type: str | None,
    channel_address: str | None,
    merged_by_id: str | None,
    roles: list[str] | None = None,
    scopes: list[str] | None = None,
    also_resolve: bool = False,
) -> ResolveConversationResult:
    if (roles is not None or scopes is not None) and not can_resolve_conversation(roles, scopes):
        return ResolveConversationResult(
            kind="forbidden",
            error_detail="Not authorized to resolve conversations",
        )
    try:
        conversation_uuid = coerce_uuid(conversation_id)
    except Exception:
        return ResolveConversationResult(kind="not_found")
    conversation = db.get(Conversation, conversation_uuid)
    if not conversation:
        return ResolveConversationResult(kind="not_found")

    resolved_channel = None
    channel_value = (channel_type or "").strip() or None
    if channel_value:
        try:
            resolved_channel = ChannelType(channel_value)
        except ValueError:
            return ResolveConversationResult(kind="invalid_channel")

    source_person_id = conversation.person_id
    try:
        conversation_service.resolve_conversation_contact(
            db,
            conversation_id=conversation_id,
            person_id=person_id.strip(),
            channel_type=resolved_channel,
            address=(channel_address or "").strip() or None,
        )
        target_person_id = coerce_uuid(person_id)
        if source_person_id and source_person_id != target_person_id:
            merged_by_value = None
            if merged_by_id:
                try:
                    merged_by_value = coerce_uuid(merged_by_id)
                except Exception:
                    merged_by_value = None
            person_service.people.merge(
                db,
                source_id=source_person_id,
                target_id=target_person_id,
                merged_by_id=merged_by_value,
            )
    except Exception as exc:
        return ResolveConversationResult(kind="error", conversation=conversation, error_detail=str(exc))

    if also_resolve:
        from app.services.crm.inbox.conversation_status import update_conversation_status

        update_conversation_status(
            db,
            conversation_id=conversation_id,
            new_status="resolved",
            actor_id=merged_by_id,
            roles=roles,
            scopes=scopes,
        )

    contact = contact_service.get_person_with_relationships(db, str(conversation.person_id))
    log_conversation_action(
        db,
        action="resolve_conversation",
        conversation_id=str(conversation.id),
        actor_id=merged_by_id,
        metadata={
            "channel_type": resolved_channel.value if resolved_channel else None,
            "channel_address": (channel_address or "").strip() or None,
        },
    )
    return ResolveConversationResult(
        kind="success",
        conversation=conversation,
        contact=contact,
    )
