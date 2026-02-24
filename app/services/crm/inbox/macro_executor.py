"""Executor for CRM conversation macros.

Dispatches each action in a macro's action list against a conversation,
with partial-failure semantics (one action failing does not stop the rest).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models.crm.enums import ConversationStatus, MacroActionType
from app.models.crm.macro import CrmConversationMacro
from app.services.common import coerce_uuid
from app.services.crm.inbox.audit import log_conversation_action

logger = logging.getLogger(__name__)


@dataclass
class MacroExecutionResult:
    ok: bool
    actions_executed: int = 0
    actions_failed: int = 0
    results: list[dict[str, Any]] = field(default_factory=list)
    error_detail: str | None = None


def _exec_assign(
    db: Session,
    conversation_id: str,
    params: dict[str, Any],
    actor_person_id: str | None,
) -> dict[str, Any]:
    from app.services.crm.conversations.service import assign_conversation

    agent_id = params.get("agent_id")
    team_id = params.get("team_id")
    assign_conversation(
        db,
        conversation_id,
        agent_id=agent_id,
        team_id=team_id,
        assigned_by_id=actor_person_id,
    )
    return {"action": "assign_conversation", "ok": True}


def _exec_set_status(
    db: Session,
    conversation_id: str,
    params: dict[str, Any],
    actor_id: str | None,
) -> dict[str, Any]:
    from app.services.crm.inbox.conversation_status import update_conversation_status

    new_status = params.get("status", "")
    # Validate the status value
    ConversationStatus(new_status)
    result = update_conversation_status(
        db,
        conversation_id=conversation_id,
        new_status=new_status,
        actor_id=actor_id,
    )
    if result.kind != "updated":
        raise ValueError(f"Status update failed: {result.detail or result.kind}")
    return {"action": "set_status", "ok": True, "status": new_status}


def _exec_add_tag(
    db: Session,
    conversation_id: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    from app.models.crm.conversation import ConversationTag

    tag = (params.get("tag") or "").strip()
    if not tag:
        raise ValueError("Tag value is required")
    existing = (
        db.query(ConversationTag)
        .filter(
            ConversationTag.conversation_id == coerce_uuid(conversation_id),
            ConversationTag.tag == tag,
        )
        .first()
    )
    if not existing:
        db.add(ConversationTag(conversation_id=coerce_uuid(conversation_id), tag=tag))
        db.flush()
    return {"action": "add_tag", "ok": True, "tag": tag}


def _exec_send_template(
    db: Session,
    conversation_id: str,
    params: dict[str, Any],
    actor_person_id: str | None,
) -> dict[str, Any]:
    from app.models.crm.conversation import Conversation
    from app.schemas.crm.inbox import InboxSendRequest
    from app.services.crm.inbox.outbound import send_message
    from app.services.crm.inbox.templates import message_templates

    template_id = params.get("template_id")
    if not template_id:
        raise ValueError("template_id is required")
    template = message_templates.get(db, template_id)
    conversation = db.get(Conversation, coerce_uuid(conversation_id))
    if not conversation:
        raise ValueError("Conversation not found")
    # Determine channel type from the latest inbound message, falling back to email.
    from app.models.crm.enums import ChannelType as CrmChannelType

    last_msg = (
        db.query(Conversation).filter_by(id=conversation.id).one().messages[-1] if conversation.messages else None
    )
    channel = last_msg.channel_type if last_msg else CrmChannelType.email
    payload = InboxSendRequest(
        conversation_id=conversation.id,
        body=template.body,
        channel_type=channel,
    )
    send_message(db, payload, author_id=actor_person_id)
    return {"action": "send_template", "ok": True, "template_id": template_id}


def execute_macro(
    db: Session,
    *,
    macro_id: str,
    conversation_id: str,
    actions: list[dict[str, Any]],
    actor_agent_id: str | None = None,
    actor_person_id: str | None = None,
) -> MacroExecutionResult:
    """Execute a macro's actions against a conversation."""
    executed = 0
    failed = 0
    results: list[dict[str, Any]] = []

    for action in actions:
        action_type = action.get("action_type", "")
        params = action.get("params", {})
        try:
            if action_type == MacroActionType.assign_conversation.value:
                result = _exec_assign(db, conversation_id, params, actor_person_id)
            elif action_type == MacroActionType.set_status.value:
                result = _exec_set_status(db, conversation_id, params, actor_person_id)
            elif action_type == MacroActionType.add_tag.value:
                result = _exec_add_tag(db, conversation_id, params)
            elif action_type == MacroActionType.send_template.value:
                result = _exec_send_template(db, conversation_id, params, actor_person_id)
            else:
                raise ValueError(f"Unknown action type: {action_type}")
            results.append(result)
            executed += 1
        except Exception as exc:
            logger.warning("Macro action %s failed: %s", action_type, exc)
            results.append({"action": action_type, "ok": False, "error": str(exc)})
            failed += 1

    # Increment execution_count
    macro = db.get(CrmConversationMacro, coerce_uuid(macro_id))
    if macro:
        macro.execution_count = (macro.execution_count or 0) + 1

    db.commit()

    log_conversation_action(
        db,
        action="execute_macro",
        conversation_id=conversation_id,
        actor_id=actor_person_id,
        metadata={"macro_id": macro_id, "executed": executed, "failed": failed},
    )

    return MacroExecutionResult(
        ok=failed == 0,
        actions_executed=executed,
        actions_failed=failed,
        results=results,
        error_detail=f"{failed} action(s) failed" if failed > 0 else None,
    )
