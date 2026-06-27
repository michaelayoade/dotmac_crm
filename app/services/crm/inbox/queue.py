"""Chat queue promotion + status.

When all available agents are at capacity (or offline), conversations wait in a
team queue (``queued_at`` set, active team-only assignment). These helpers pull
the oldest-waiting conversations onto agents as capacity frees up — both reactively
(when an agent resolves a chat) and via a periodic sweep.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.logging import get_logger
from app.models.crm.conversation import Conversation, ConversationAssignment
from app.models.crm.enums import ConversationStatus
from app.models.crm.team import CrmAgent, CrmAgentTeam
from app.services.crm.inbox import routing

logger = get_logger(__name__)

_QUEUEABLE_STATUSES = (ConversationStatus.open, ConversationStatus.pending)


def _agent_active_chats(db: Session, agent_id) -> int:
    return routing._agent_active_chat_counts(db, [agent_id]).get(agent_id, 0)


def _oldest_queued_in_teams(db: Session, team_ids: list) -> Conversation | None:
    if not team_ids:
        return None
    return (
        db.query(Conversation)
        .join(ConversationAssignment, ConversationAssignment.conversation_id == Conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .filter(ConversationAssignment.agent_id.is_(None))
        .filter(ConversationAssignment.team_id.in_(team_ids))
        .filter(Conversation.is_active.is_(True))
        .filter(Conversation.queued_at.isnot(None))
        .filter(Conversation.status.in_(_QUEUEABLE_STATUSES))
        .order_by(Conversation.queued_at.asc())
        .first()
    )


def promote_next_for_agent(db: Session, agent_id) -> int:
    """Pull the oldest queued conversation(s) in the agent's teams onto the agent,
    up to their capacity. Called when an agent frees a slot (resolve/close).

    Locks the agent row so the cap is enforced authoritatively against concurrent
    promotions. Best-effort: never raises. Returns the number promoted.
    """
    promoted = 0
    try:
        agent = db.query(CrmAgent).filter(CrmAgent.id == agent_id).with_for_update().first()
        if agent is None or not agent.is_active:
            return 0
        if not routing._agent_is_assignable(db, str(agent.id)):
            return 0

        cap = routing._agent_cap(agent, routing._global_max_concurrent(db))
        team_ids = [
            row[0]
            for row in db.query(CrmAgentTeam.team_id)
            .filter(CrmAgentTeam.agent_id == agent.id)
            .filter(CrmAgentTeam.is_active.is_(True))
            .all()
        ]

        while _agent_active_chats(db, agent.id) < cap:
            conversation = _oldest_queued_in_teams(db, team_ids)
            if conversation is None:
                break
            active = (
                db.query(ConversationAssignment)
                .filter(ConversationAssignment.conversation_id == conversation.id)
                .filter(ConversationAssignment.is_active.is_(True))
                .first()
            )
            team_id = str(active.team_id) if active and active.team_id else None
            if team_id is None:
                break
            from app.services.crm import conversation as conversation_service

            conversation_service.assign_conversation(
                db,
                conversation_id=str(conversation.id),
                agent_id=str(agent.id),
                team_id=team_id,
                assigned_by_id=None,
                update_lead_owner=False,
            )
            promoted += 1
    except Exception:
        logger.exception("promote_next_for_agent_failed agent_id=%s", agent_id)
    return promoted


def promote_queued_conversations(db: Session, *, limit: int = 200) -> dict[str, Any]:
    """Periodic FIFO sweep: assign the oldest-waiting queued conversations to any
    available agent. Generalizes the AI-only retry to dialog- and rule-routed
    chats; AI-owned conversations are skipped (the AI retry task drives those)."""
    rows = (
        db.query(Conversation, ConversationAssignment.team_id)
        .join(ConversationAssignment, ConversationAssignment.conversation_id == Conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .filter(ConversationAssignment.agent_id.is_(None))
        .filter(ConversationAssignment.team_id.isnot(None))
        .filter(Conversation.is_active.is_(True))
        .filter(Conversation.queued_at.isnot(None))
        .filter(Conversation.status.in_(_QUEUEABLE_STATUSES))
        .order_by(Conversation.queued_at.asc())
        .limit(limit)
        .all()
    )

    promoted = 0
    still_queued = 0
    errors: list[str] = []
    for conversation, team_id in rows:
        meta = conversation.metadata_ if isinstance(conversation.metadata_, dict) else {}
        if meta.get("ai_intake"):
            # Owned by the AI intake retry task; don't double-drive.
            continue
        try:
            assignment = routing.assign_or_enqueue(
                db,
                conversation=conversation,
                team_id=str(team_id),
                assigned_by_id=None,
            )
            if assignment is not None and assignment.agent_id is not None:
                promoted += 1
            else:
                still_queued += 1
        except Exception as exc:
            db.rollback()
            errors.append(f"{conversation.id}: {exc}")

    return {"scanned": len(rows), "promoted": promoted, "still_queued": still_queued, "errors": errors}


DEFAULT_HANDLE_SECONDS = 300


def _active_assignment(db: Session, conversation_id) -> ConversationAssignment | None:
    return (
        db.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conversation_id)
        .filter(ConversationAssignment.is_active.is_(True))
        .first()
    )


def _avg_handle_seconds(db: Session, team_id) -> float:
    """Average recent handle time for a team, used to estimate queue wait."""
    from sqlalchemy import func

    cutoff = datetime.now(UTC) - timedelta(hours=24)
    avg = (
        db.query(func.avg(Conversation.resolution_time_seconds))
        .join(ConversationAssignment, ConversationAssignment.conversation_id == Conversation.id)
        .filter(ConversationAssignment.team_id == team_id)
        .filter(Conversation.resolution_time_seconds.isnot(None))
        .filter(Conversation.resolved_at.isnot(None))
        .filter(Conversation.resolved_at >= cutoff)
        .scalar()
    )
    return float(avg) if avg else float(DEFAULT_HANDLE_SECONDS)


def queue_status_for_conversation(db: Session, conversation: Conversation) -> dict[str, Any] | None:
    """Queue position + estimated wait for a conversation still waiting in a team
    queue. Returns ``None`` when it is not queued (has an agent, or never queued)."""
    if conversation.queued_at is None:
        return None
    assignment = _active_assignment(db, conversation.id)
    if assignment is None or assignment.agent_id is not None or assignment.team_id is None:
        return None
    team_id = assignment.team_id

    ahead = (
        db.query(Conversation.id)
        .join(ConversationAssignment, ConversationAssignment.conversation_id == Conversation.id)
        .filter(ConversationAssignment.is_active.is_(True))
        .filter(ConversationAssignment.agent_id.is_(None))
        .filter(ConversationAssignment.team_id == team_id)
        .filter(Conversation.is_active.is_(True))
        .filter(Conversation.status.in_(_QUEUEABLE_STATUSES))
        .filter(Conversation.queued_at.isnot(None))
        .filter(Conversation.queued_at < conversation.queued_at)
        .count()
    )
    position = ahead + 1

    available_agents = max(len(routing._list_active_agents(db, str(team_id))), 1)
    avg_handle = _avg_handle_seconds(db, team_id)
    estimated_wait_seconds = int(position * avg_handle / available_agents)

    return {"position": position, "estimated_wait_seconds": estimated_wait_seconds}
