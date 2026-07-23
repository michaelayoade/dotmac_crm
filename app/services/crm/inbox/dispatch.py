"""Authoritative two-queue CRM chat dispatcher.

This module is deliberately the only automatic writer of agent assignments
while ``crm_two_queue_dispatch_enabled`` is enabled.  AI intake may classify
and converse with the customer, but it calls ``enqueue_classified`` rather than
selecting an agent itself.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.enums import (
    AgentPresenceStatus,
    ChannelType,
    ConversationQueueState,
    ConversationQueueType,
    ConversationStatus,
    MessageDirection,
)
from app.models.crm.presence import AgentPresence
from app.models.crm.queue import ConversationQueueEntry, ConversationQueueEvent
from app.models.crm.team import CrmAgent, CrmAgentTeam, CrmTeam
from app.schemas.crm.inbox import InboxSendRequest
from app.services.common import coerce_uuid
from app.services.crm.inbox import routing
from app.services.crm.inbox.audit import log_conversation_action
from app.services.crm.inbox.outbound import send_message
from app.services.crm.presence import DEFAULT_STALE_MINUTES
from app.services.crm.presence import agent_presence as presence_service

SUPPORTED_CHANNELS = {
    ChannelType.whatsapp,
    ChannelType.facebook_messenger,
    ChannelType.instagram_dm,
    ChannelType.chat_widget,
}
ACTIVE_QUEUE_STATES = {
    ConversationQueueState.classifying,
    ConversationQueueState.waiting,
    ConversationQueueState.assigned,
}
QUEUEABLE_STATUSES = {ConversationStatus.open, ConversationStatus.pending}
HARD_MAX_CONCURRENT_CHATS = 20
SUPPORT_TEAM_NAMES = {"helpdesk", "technical support"}
SALES_TEAM_NAMES = {"sales call center"}


def enabled(db: Session) -> bool:
    from app.services.settings_spec import SettingDomain, resolve_value

    return bool(resolve_value(db, SettingDomain.notification, "crm_two_queue_dispatch_enabled"))


def queue_for_department(department: str | None) -> ConversationQueueType:
    """Map AI's detailed departments to the two customer queues."""
    normalized = (department or "").strip().lower()
    if normalized == "sales" or normalized == "billing" or normalized.startswith("billing_"):
        return ConversationQueueType.sales
    return ConversationQueueType.support


def _event(
    entry: ConversationQueueEntry, event_type: str, *, actor_id=None, payload: dict | None = None
) -> ConversationQueueEvent:
    return ConversationQueueEvent(
        queue_entry_id=entry.id,
        event_type=event_type,
        actor_id=coerce_uuid(actor_id) if actor_id else None,
        payload=payload or {},
    )


def _arrival_time(db: Session, conversation: Conversation) -> datetime:
    inbound = (
        db.query(Message)
        .filter(Message.conversation_id == conversation.id, Message.direction == MessageDirection.inbound)
        .order_by(func.coalesce(Message.received_at, Message.created_at).asc())
        .first()
    )
    value = (
        (inbound.received_at if inbound else None)
        or (inbound.created_at if inbound else None)
        or conversation.created_at
    )
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def active_entry(db: Session, conversation_id: str, *, lock: bool = False) -> ConversationQueueEntry | None:
    query = db.query(ConversationQueueEntry).filter(
        ConversationQueueEntry.conversation_id == coerce_uuid(conversation_id),
        ConversationQueueEntry.state.in_(ACTIVE_QUEUE_STATES),
    )
    return query.with_for_update().first() if lock else query.first()


def enqueue_classified(
    db: Session,
    *,
    conversation: Conversation,
    queue_type: ConversationQueueType,
    classification_attempts: int = 0,
    source: str = "ai_classification",
    notify_initial: bool = True,
) -> ConversationQueueEntry:
    """Create or update exactly one live queue cycle without choosing an agent."""
    entry = active_entry(db, str(conversation.id), lock=True)
    if entry is None:
        entry = ConversationQueueEntry(
            conversation_id=conversation.id,
            queue_type=queue_type,
            state=ConversationQueueState.waiting,
            original_arrival_at=_arrival_time(db, conversation),
            classification_attempts=classification_attempts,
            metadata_={"source": source},
        )
        db.add(entry)
        db.flush()
        db.add(_event(entry, "enqueued", payload={"queue_type": queue_type.value, "source": source}))
    else:
        entry.queue_type = queue_type
        entry.classification_attempts = max(entry.classification_attempts, classification_attempts)
        if entry.state == ConversationQueueState.classifying:
            entry.state = ConversationQueueState.waiting
        db.add(_event(entry, "classified", payload={"queue_type": queue_type.value, "source": source}))
    conversation.status = ConversationStatus.pending
    db.flush()
    position = position_for_entry(db, entry)
    if notify_initial and position is not None:
        _notice(
            db,
            entry,
            key="initial",
            body=f"Your request is in the {entry.queue_type.value.title()} queue. Your current position is {position}.",
        )
    return entry


def backfill_unresolved(db: Session, *, limit: int = 500) -> dict[str, int]:
    """Create one queue cycle for legacy unresolved chats without replaying notices."""
    conversations = (
        db.query(Conversation)
        .filter(Conversation.is_active.is_(True), Conversation.status.in_(QUEUEABLE_STATUSES))
        .order_by(Conversation.created_at.asc())
        .limit(limit)
        .all()
    )
    created = skipped = 0
    for conversation in conversations:
        if active_entry(db, str(conversation.id)) is not None:
            skipped += 1
            continue
        assignment = (
            db.query(ConversationAssignment)
            .filter(
                ConversationAssignment.conversation_id == conversation.id, ConversationAssignment.is_active.is_(True)
            )
            .first()
        )
        metadata = conversation.metadata_ if isinstance(conversation.metadata_, dict) else {}
        ai_state = metadata.get("ai_intake") if isinstance(metadata.get("ai_intake"), dict) else {}
        department = str(ai_state.get("department") or "")
        team_name = ""
        if assignment and assignment.team_id:
            team = db.get(CrmTeam, assignment.team_id)
            team_name = team.name if team else ""
        ambiguous = not department and not team_name
        queue_type = (
            ConversationQueueType.sales
            if queue_for_department(department) == ConversationQueueType.sales
            or team_name.strip().lower() in SALES_TEAM_NAMES
            else ConversationQueueType.support
        )
        entry = enqueue_classified(
            db,
            conversation=conversation,
            queue_type=queue_type,
            source="legacy_backfill",
            notify_initial=False,
        )
        if assignment and assignment.agent_id:
            entry.current_agent_id = assignment.agent_id
            entry.assigned_at = assignment.assigned_at
            entry.state = ConversationQueueState.assigned
        if ambiguous:
            db.add(_event(entry, "backfill_ambiguous", payload={"fallback_queue": "support"}))
            log_conversation_action(
                db,
                action="queue_backfill_ambiguous",
                conversation_id=str(conversation.id),
                actor_id=None,
                metadata={"fallback_queue": "support"},
            )
        else:
            position = position_for_entry(db, entry)
            if position is not None:
                _notice(
                    db,
                    entry,
                    key="cutover_position",
                    body=f"Your current position in the {entry.queue_type.value.title()} queue is {position}.",
                )
        created += 1
    db.commit()
    return {"created": created, "skipped": skipped}


def begin_classification(db: Session, *, conversation: Conversation) -> ConversationQueueEntry:
    entry = active_entry(db, str(conversation.id), lock=True)
    if entry is not None:
        return entry
    entry = ConversationQueueEntry(
        conversation_id=conversation.id,
        queue_type=ConversationQueueType.support,
        state=ConversationQueueState.classifying,
        original_arrival_at=_arrival_time(db, conversation),
    )
    db.add(entry)
    db.flush()
    db.add(_event(entry, "classification_started"))
    return entry


def _pool_team_ids(db: Session, queue_type: ConversationQueueType) -> list:
    names = SUPPORT_TEAM_NAMES if queue_type == ConversationQueueType.support else SALES_TEAM_NAMES
    return [
        row[0]
        for row in db.query(CrmTeam.id).filter(func.lower(CrmTeam.name).in_(names), CrmTeam.is_active.is_(True)).all()
    ]


def _agent_cap(agent: CrmAgent) -> int:
    configured = agent.max_concurrent_chats if agent.max_concurrent_chats is not None else HARD_MAX_CONCURRENT_CHATS
    return min(max(int(configured), 1), HARD_MAX_CONCURRENT_CHATS)


def _eligible_agents(db: Session, queue_type: ConversationQueueType) -> list[CrmAgent]:
    team_ids = _pool_team_ids(db, queue_type)
    if not team_ids:
        return []
    cutoff = datetime.now(UTC) - timedelta(minutes=DEFAULT_STALE_MINUTES)
    agents = (
        db.query(CrmAgent)
        .join(CrmAgentTeam, CrmAgentTeam.agent_id == CrmAgent.id)
        .join(AgentPresence, AgentPresence.agent_id == CrmAgent.id)
        .filter(CrmAgentTeam.team_id.in_(team_ids), CrmAgentTeam.is_active.is_(True), CrmAgent.is_active.is_(True))
        .filter(AgentPresence.manual_override_status.is_(None))
        .filter(AgentPresence.status.in_([AgentPresenceStatus.online, AgentPresenceStatus.away]))
        .filter(AgentPresence.last_seen_at.isnot(None), AgentPresence.last_seen_at >= cutoff)
        .order_by(CrmAgent.created_at.asc(), CrmAgent.id.asc())
        .distinct()
        .all()
    )
    loads = routing._agent_active_chat_counts(db, [agent.id for agent in agents])
    return [agent for agent in agents if loads.get(agent.id, 0) < _agent_cap(agent)]


def _round_robin_agent(db: Session, queue_type: ConversationQueueType) -> CrmAgent | None:
    candidates = _eligible_agents(db, queue_type)
    if not candidates:
        return None
    last = (
        db.query(ConversationQueueEntry.current_agent_id)
        .filter(ConversationQueueEntry.queue_type == queue_type)
        .filter(ConversationQueueEntry.state == ConversationQueueState.assigned)
        .filter(ConversationQueueEntry.current_agent_id.isnot(None))
        .order_by(ConversationQueueEntry.assigned_at.desc())
        .first()
    )
    ids = [agent.id for agent in candidates]
    if last and last[0] in ids:
        return candidates[(ids.index(last[0]) + 1) % len(candidates)]
    return candidates[0]


def position_for_entry(db: Session, entry: ConversationQueueEntry) -> int | None:
    if entry.state != ConversationQueueState.waiting:
        return None
    ahead = (
        db.query(ConversationQueueEntry.id)
        .filter(ConversationQueueEntry.queue_type == entry.queue_type)
        .filter(ConversationQueueEntry.state == ConversationQueueState.waiting)
        .filter(
            (ConversationQueueEntry.original_arrival_at < entry.original_arrival_at)
            | (
                (ConversationQueueEntry.original_arrival_at == entry.original_arrival_at)
                & (ConversationQueueEntry.id < entry.id)
            )
        )
        .count()
    )
    return ahead + 1


def _notice(db: Session, entry: ConversationQueueEntry, *, key: str, body: str) -> bool:
    """Deliver an idempotent system notice which cannot count as a human reply."""
    ledger = dict(entry.notification_ledger or {})
    if ledger.get(key):
        return False
    inbound = (
        db.query(Message)
        .filter(Message.conversation_id == entry.conversation_id, Message.direction == MessageDirection.inbound)
        .filter(Message.channel_type.in_(SUPPORTED_CHANNELS))
        .order_by(func.coalesce(Message.received_at, Message.created_at).desc())
        .first()
    )
    if inbound is None:
        return False
    send_message(
        db,
        InboxSendRequest(
            conversation_id=entry.conversation_id,
            channel_type=inbound.channel_type,
            channel_target_id=inbound.channel_target_id,
            body=body,
            metadata={"queue_notice": True, "ai_intake_generated": True, "queue_notice_key": key},
        ),
        author_id=None,
        trace_id="two-queue-dispatch",
    )
    ledger[key] = datetime.now(UTC).isoformat()
    entry.notification_ledger = ledger
    db.flush()
    return True


def emit_position_notices(db: Session, *, limit: int = 500) -> int:
    """Emit configured queue milestones once; position one is never announced."""
    sent = 0
    entries = (
        db.query(ConversationQueueEntry)
        .filter(ConversationQueueEntry.state == ConversationQueueState.waiting)
        .order_by(ConversationQueueEntry.original_arrival_at.asc(), ConversationQueueEntry.id.asc())
        .limit(limit)
        .all()
    )
    for entry in entries:
        position = position_for_entry(db, entry)
        if position in {20, 10, 5, 3, 2} and _notice(
            db,
            entry,
            key=f"position:{position}",
            body=f"Your current position in the {entry.queue_type.value.title()} queue is {position}.",
        ):
            sent += 1
    db.commit()
    return sent


def _end_assignment(db: Session, conversation_id, *, now: datetime) -> None:
    db.query(ConversationAssignment).filter(
        ConversationAssignment.conversation_id == conversation_id,
        ConversationAssignment.is_active.is_(True),
    ).update({"is_active": False, "ended_at": now, "updated_at": now})


def _assign_entry(db: Session, entry: ConversationQueueEntry, agent: CrmAgent, *, actor_id: str | None = None) -> None:
    """Create an assignment stint after the agent row has been locked and rechecked."""
    agent = db.query(CrmAgent).filter(CrmAgent.id == agent.id).with_for_update().one()
    if routing._agent_active_chat_counts(db, [agent.id]).get(agent.id, 0) >= _agent_cap(agent):
        raise HTTPException(status_code=409, detail="Agent is at the 20-chat capacity limit")
    conversation = db.query(Conversation).filter(Conversation.id == entry.conversation_id).with_for_update().one()
    now = datetime.now(UTC)
    _end_assignment(db, conversation.id, now=now)
    assignment = ConversationAssignment(
        conversation_id=conversation.id,
        agent_id=agent.id,
        assigned_by_id=coerce_uuid(actor_id) if actor_id else None,
        assigned_at=now,
        is_active=True,
    )
    db.add(assignment)
    entry.previous_agent_id = entry.current_agent_id
    entry.current_agent_id = agent.id
    entry.assigned_at = now
    entry.state = ConversationQueueState.assigned
    conversation.status = ConversationStatus.open
    if conversation.first_assigned_at is None:
        conversation.first_assigned_at = now
    db.add(_event(entry, "assigned", actor_id=actor_id, payload={"agent_id": str(agent.id)}))
    _notice(db, entry, key="assigned", body="An agent has now been assigned to your chat.")


def dispatch_waiting(db: Session, *, limit: int = 200) -> dict[str, int]:
    """Fill available capacity strictly from the FIFO head of each logical queue."""
    assigned = 0
    scanned = 0
    for queue_type in ConversationQueueType:
        while scanned < limit:
            entry = (
                db.query(ConversationQueueEntry)
                .filter(
                    ConversationQueueEntry.queue_type == queue_type,
                    ConversationQueueEntry.state == ConversationQueueState.waiting,
                )
                .order_by(ConversationQueueEntry.original_arrival_at.asc(), ConversationQueueEntry.id.asc())
                .with_for_update(skip_locked=True)
                .first()
            )
            if entry is None:
                break
            scanned += 1
            agent = _round_robin_agent(db, queue_type)
            if agent is None:
                break
            try:
                _assign_entry(db, entry, agent)
                assigned += 1
                db.commit()
            except Exception:
                db.rollback()
                raise
    return {"scanned": scanned, "assigned": assigned}


def manager_assign_head(db: Session, *, conversation_id: str, agent_id: str, actor_id: str) -> ConversationQueueEntry:
    entry = active_entry(db, conversation_id, lock=True)
    if entry is None or entry.state != ConversationQueueState.waiting:
        raise HTTPException(status_code=409, detail="Conversation is not waiting in a dispatch queue")
    position = position_for_entry(db, entry)
    if position != 1:
        raise HTTPException(
            status_code=409, detail=f"Only the FIFO head may be assigned; this chat is position {position}"
        )
    agent = db.query(CrmAgent).filter(CrmAgent.id == coerce_uuid(agent_id), CrmAgent.is_active.is_(True)).first()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found or inactive")
    presence = db.query(AgentPresence).filter(AgentPresence.agent_id == agent.id).first()
    if presence_service.effective_status(presence) not in {AgentPresenceStatus.online, AgentPresenceStatus.away}:
        raise HTTPException(status_code=409, detail="Agent is offline or unavailable")
    _assign_entry(db, entry, agent, actor_id=actor_id)
    log_conversation_action(
        db,
        action="queue_manager_assign",
        conversation_id=conversation_id,
        actor_id=actor_id,
        metadata={"queue": entry.queue_type.value},
    )
    db.commit()
    return entry


def transfer(
    db: Session, *, conversation_id: str, queue_type: ConversationQueueType, actor_id: str
) -> ConversationQueueEntry:
    entry = active_entry(db, conversation_id, lock=True)
    if entry is None:
        raise HTTPException(status_code=409, detail="Conversation has no active queue cycle")
    now = datetime.now(UTC)
    _end_assignment(db, entry.conversation_id, now=now)
    previous = entry.queue_type
    entry.previous_agent_id = entry.current_agent_id
    entry.current_agent_id = None
    entry.assigned_at = None
    entry.queue_type = queue_type
    entry.state = ConversationQueueState.waiting
    db.add(
        _event(
            entry, "manager_transferred", actor_id=actor_id, payload={"from": previous.value, "to": queue_type.value}
        )
    )
    position = position_for_entry(db, entry)
    if position is not None:
        _notice(
            db,
            entry,
            key=f"transfer:{previous.value}:{queue_type.value}:{entry.updated_at.isoformat()}",
            body=(
                f"Your request has been moved to the {queue_type.value.title()} queue. "
                f"Your current position is {position}."
            ),
        )
    log_conversation_action(
        db,
        action="queue_transfer",
        conversation_id=conversation_id,
        actor_id=actor_id,
        metadata={"from": previous.value, "to": queue_type.value},
    )
    db.commit()
    return entry


def requeue_entry(db: Session, entry: ConversationQueueEntry, *, reason: str) -> None:
    now = datetime.now(UTC)
    _end_assignment(db, entry.conversation_id, now=now)
    entry.previous_agent_id = entry.current_agent_id
    entry.current_agent_id = None
    entry.assigned_at = None
    entry.state = ConversationQueueState.waiting
    db.add(_event(entry, "requeued", payload={"reason": reason}))
    position = position_for_entry(db, entry)
    if reason == "offline":
        body = "Your assigned agent became unavailable. Your original waiting priority has been preserved."
        if position is not None:
            body += f" Your current position is {position}."
        _notice(db, entry, key=f"offline_requeue:{now.isoformat()}", body=body)
    elif reason == "missed_first_response":
        _notice(
            db,
            entry,
            key=f"missed_response_requeue:{now.isoformat()}",
            body=(
                "We are returning your request to the queue because your assigned agent did not respond in time. "
                "Your original waiting priority has been preserved."
            ),
        )
        log_conversation_action(
            db,
            action="queue_manager_alert_missed_response",
            conversation_id=str(entry.conversation_id),
            actor_id=None,
            metadata={"queue": entry.queue_type.value, "previous_agent_id": str(entry.previous_agent_id)},
        )


def recover_unavailable_and_missed(db: Session, *, limit: int = 200) -> dict[str, int]:
    """Return chats after ten minutes offline or twenty minutes without a human reply."""
    now = datetime.now(UTC)
    rows = (
        db.query(ConversationQueueEntry, Conversation)
        .join(Conversation, Conversation.id == ConversationQueueEntry.conversation_id)
        .filter(ConversationQueueEntry.state == ConversationQueueState.assigned)
        .filter(Conversation.status.in_(QUEUEABLE_STATUSES))
        .order_by(ConversationQueueEntry.assigned_at.asc())
        .limit(limit)
        .all()
    )
    offline = missed = 0
    for entry, _conversation in rows:
        assignment = (
            db.query(ConversationAssignment)
            .filter(
                ConversationAssignment.conversation_id == entry.conversation_id,
                ConversationAssignment.is_active.is_(True),
            )
            .first()
        )
        if assignment is None or assignment.first_response_at is not None:
            continue
        presence = db.query(AgentPresence).filter(AgentPresence.agent_id == entry.current_agent_id).first()
        effective = presence_service.effective_status(presence) if presence else AgentPresenceStatus.offline
        assigned_at = entry.assigned_at or now
        offline_since = presence.updated_at if presence and effective == AgentPresenceStatus.offline else None
        if offline_since and now - offline_since >= timedelta(minutes=10):
            requeue_entry(db, entry, reason="offline")
            offline += 1
        elif now - assigned_at >= timedelta(minutes=20):
            requeue_entry(db, entry, reason="missed_first_response")
            missed += 1
    db.commit()
    return {"offline_requeued": offline, "missed_response_requeued": missed}


def complete_cycle(db: Session, *, conversation_id: str) -> None:
    entry = active_entry(db, conversation_id, lock=True)
    if entry is None:
        return
    entry.state = ConversationQueueState.completed
    entry.completed_at = datetime.now(UTC)
    entry.is_active = False
    db.add(_event(entry, "completed"))
    db.commit()


def queue_payload(db: Session, conversation: Conversation) -> dict | None:
    entry = active_entry(db, str(conversation.id))
    if entry is None:
        return None
    return {
        "queue": entry.queue_type.value,
        "state": entry.state.value,
        "position": position_for_entry(db, entry),
    }


def dashboard_snapshot(db: Session, *, queue_limit: int = 100) -> dict:
    """Read model for the manager queue dashboard."""
    now = datetime.now(UTC)
    queues: list[dict] = []
    for queue_type in ConversationQueueType:
        waiting = (
            db.query(ConversationQueueEntry)
            .filter(
                ConversationQueueEntry.queue_type == queue_type,
                ConversationQueueEntry.state == ConversationQueueState.waiting,
            )
            .order_by(ConversationQueueEntry.original_arrival_at.asc(), ConversationQueueEntry.id.asc())
            .limit(queue_limit)
            .all()
        )
        oldest_wait_seconds = int((now - waiting[0].original_arrival_at).total_seconds()) if waiting else 0
        queues.append(
            {
                "type": queue_type.value,
                "depth": len(waiting),
                "oldest_wait_seconds": max(oldest_wait_seconds, 0),
                "entries": [
                    {
                        "id": str(entry.id),
                        "conversation_id": str(entry.conversation_id),
                        "arrival_at": entry.original_arrival_at,
                        "position": index,
                    }
                    for index, entry in enumerate(waiting, start=1)
                ],
            }
        )
    agents = []
    seen: set = set()
    for queue_type in ConversationQueueType:
        for agent in _eligible_agents(db, queue_type):
            if agent.id in seen:
                continue
            seen.add(agent.id)
            agents.append(
                {
                    "id": str(agent.id),
                    "queue": queue_type.value,
                    "load": routing._agent_active_chat_counts(db, [agent.id]).get(agent.id, 0),
                    "cap": _agent_cap(agent),
                }
            )
    holds = (
        db.query(ConversationQueueEntry)
        .filter(ConversationQueueEntry.state == ConversationQueueState.classifying)
        .count()
    )
    return {"queues": queues, "agents": agents, "classification_holds": holds}
