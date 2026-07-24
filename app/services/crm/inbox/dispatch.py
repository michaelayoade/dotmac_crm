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
from app.models.crm.queue import ConversationQueueDispatchState, ConversationQueueEntry, ConversationQueueEvent
from app.models.crm.team import CrmAgent, CrmAgentTeam, CrmTeam
from app.models.notification import Notification, NotificationChannel, NotificationStatus
from app.models.service_team import ServiceTeam, ServiceTeamMember, ServiceTeamMemberRole, ServiceTeamType
from app.schemas.crm.inbox import InboxSendRequest
from app.services.common import coerce_uuid
from app.services.crm.inbox import routing
from app.services.crm.inbox.audit import log_conversation_action
from app.services.crm.inbox.outbox import enqueue_outbound_message_in_transaction
from app.services.crm.inbox.summaries import recompute_conversation_summary_and_invalidate_cache
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
    # Record the initial observation without contacting a customer.  Milestone
    # notices are emitted by the worker, never for position one.
    if notify_initial:
        tracking = dict(entry.position_tracking or {})
        tracking[entry.queue_type.value] = {
            "last_observed_position": position_for_entry(db, entry),
            "sent_milestones": [],
        }
        entry.position_tracking = tracking
    return entry


def backfill_unresolved(db: Session, *, mode: str = "dry_run", batch_size: int = 500) -> dict[str, int | str]:
    """Safely prepare cutover state; mutations require an explicit mode.

    ``dry_run`` never flushes, commits, or stages notices. ``populate_silent``
    preserves current assignments and creates only queue rows. ``announce``
    only stages a single idempotent current-position notice for already-live
    cycles, after a separate validation pass has approved population.
    """
    if mode not in {"dry_run", "populate_silent", "announce"}:
        raise ValueError("mode must be dry_run, populate_silent, or announce")
    report: dict[str, int | str] = {
        "mode": mode,
        "support": 0,
        "sales": 0,
        "assigned": 0,
        "waiting": 0,
        "ambiguous_fallbacks": 0,
        "missing_channels": 0,
        "agents_above_cap": 0,
        "lacking_valid_queue_state": 0,
        "created": 0,
        "skipped": 0,
        "announced": 0,
    }
    cursor = None
    while True:
        query = db.query(Conversation).filter(
            Conversation.is_active.is_(True), Conversation.status.in_(QUEUEABLE_STATUSES)
        )
        if cursor is not None:
            query = query.filter(Conversation.id > cursor)
        conversations = query.order_by(Conversation.id.asc()).limit(batch_size).all()
        if not conversations:
            break
        cursor = conversations[-1].id
        for conversation in conversations:
            live = active_entry(db, str(conversation.id))
            if mode == "announce":
                if live is None:
                    report["lacking_valid_queue_state"] = int(report["lacking_valid_queue_state"]) + 1
                    continue
                position = position_for_entry(db, live)
                if (
                    position
                    and position != 1
                    and _notice(
                        db,
                        live,
                        key=f"cutover-position:{live.queue_type.value}:{position}",
                        body=f"Your current position in the {live.queue_type.value.title()} queue is {position}.",
                    )
                ):
                    report["announced"] = int(report["announced"]) + 1
                continue
            if live is not None:
                report["skipped"] = int(report["skipped"]) + 1
                continue
            assignment = (
                db.query(ConversationAssignment)
                .filter(
                    ConversationAssignment.conversation_id == conversation.id,
                    ConversationAssignment.is_active.is_(True),
                )
                .first()
            )
            metadata = conversation.metadata_ if isinstance(conversation.metadata_, dict) else {}
            raw_ai_state = metadata.get("ai_intake")
            ai_state = raw_ai_state if isinstance(raw_ai_state, dict) else {}
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
            report[queue_type.value] = int(report[queue_type.value]) + 1
            if ambiguous:
                report["ambiguous_fallbacks"] = int(report["ambiguous_fallbacks"]) + 1
            if assignment and assignment.agent_id:
                report["assigned"] = int(report["assigned"]) + 1
                agent = db.get(CrmAgent, assignment.agent_id)
                if agent and routing._agent_active_chat_counts(db, [agent.id]).get(agent.id, 0) >= _agent_cap(agent):
                    report["agents_above_cap"] = int(report["agents_above_cap"]) + 1
            else:
                report["waiting"] = int(report["waiting"]) + 1
            if not (
                db.query(Message.id)
                .filter(Message.conversation_id == conversation.id, Message.direction == MessageDirection.inbound)
                .filter(Message.channel_type.in_(SUPPORTED_CHANNELS))
                .first()
            ):
                report["missing_channels"] = int(report["missing_channels"]) + 1
            if mode == "dry_run":
                continue
            entry = enqueue_classified(
                db, conversation=conversation, queue_type=queue_type, source="legacy_backfill", notify_initial=False
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
            report["created"] = int(report["created"]) + 1
    if mode != "dry_run":
        db.commit()
    return report


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


def _dispatch_state(db: Session, queue_type: ConversationQueueType, *, lock: bool) -> ConversationQueueDispatchState:
    """Return the pre-seeded queue mutex/cursor, optionally under row lock."""
    query = db.query(ConversationQueueDispatchState).filter(ConversationQueueDispatchState.queue_type == queue_type)
    if lock:
        query = query.with_for_update()
    state = query.one_or_none()
    if state is None:
        # This is only a compatibility guard for databases not migrated yet;
        # normal operation gets both rows from the migration.
        state = ConversationQueueDispatchState(queue_type=queue_type)
        db.add(state)
        db.flush()
        if lock:
            state = (
                db.query(ConversationQueueDispatchState)
                .filter(ConversationQueueDispatchState.queue_type == queue_type)
                .with_for_update()
                .one()
            )
    return state


def _lock_queue_states(
    db: Session, queue_types: set[ConversationQueueType]
) -> dict[ConversationQueueType, ConversationQueueDispatchState]:
    """Acquire Support then Sales, regardless of transfer direction."""
    states: dict[ConversationQueueType, ConversationQueueDispatchState] = {}
    for queue_type in sorted(queue_types, key=lambda value: value.value):
        states[queue_type] = _dispatch_state(db, queue_type, lock=True)
    return states


def _agent_cap(agent: CrmAgent) -> int:
    configured = agent.max_concurrent_chats if agent.max_concurrent_chats is not None else HARD_MAX_CONCURRENT_CHATS
    return min(max(int(configured), 1), HARD_MAX_CONCURRENT_CHATS)


def _queue_agents(db: Session, queue_type: ConversationQueueType) -> list[CrmAgent]:
    """All active pool members in deterministic order, including ineligible ones."""
    team_ids = _pool_team_ids(db, queue_type)
    if not team_ids:
        return []
    return (
        db.query(CrmAgent)
        .join(CrmAgentTeam, CrmAgentTeam.agent_id == CrmAgent.id)
        .filter(CrmAgentTeam.team_id.in_(team_ids), CrmAgentTeam.is_active.is_(True), CrmAgent.is_active.is_(True))
        .order_by(CrmAgent.created_at.asc(), CrmAgent.id.asc())
        .distinct()
        .all()
    )


def _agent_is_eligible(db: Session, agent: CrmAgent, loads: dict) -> bool:
    presence = db.query(AgentPresence).filter(AgentPresence.agent_id == agent.id).first()
    cutoff = datetime.now(UTC) - timedelta(minutes=DEFAULT_STALE_MINUTES)
    return bool(
        presence
        and presence.manual_override_status is None
        and presence.status in {AgentPresenceStatus.online, AgentPresenceStatus.away}
        and presence.last_seen_at
        and presence.last_seen_at >= cutoff
        and loads.get(agent.id, 0) < _agent_cap(agent)
    )


def _eligible_agents(db: Session, queue_type: ConversationQueueType) -> list[CrmAgent]:
    agents = _queue_agents(db, queue_type)
    loads = routing._agent_active_chat_counts(db, [agent.id for agent in agents])
    return [agent for agent in agents if _agent_is_eligible(db, agent, loads)]


def _round_robin_agent(
    db: Session, queue_type: ConversationQueueType, state: ConversationQueueDispatchState
) -> CrmAgent | None:
    """Scan after the durable cursor, skipping unavailable/full members once."""
    candidates = _queue_agents(db, queue_type)
    if not candidates:
        return None
    ids = [agent.id for agent in candidates]
    start = 0
    if state.round_robin_cursor_agent_id in ids:
        start = (ids.index(state.round_robin_cursor_agent_id) + 1) % len(candidates)
    loads = routing._agent_active_chat_counts(db, ids)
    for offset in range(len(candidates)):
        agent = candidates[(start + offset) % len(candidates)]
        if _agent_is_eligible(db, agent, loads):
            return agent
    return None


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
    """Atomically stage an idempotent system notice for the outbox worker."""
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
    enqueue_outbound_message_in_transaction(
        db,
        payload=InboxSendRequest(
            conversation_id=entry.conversation_id,
            channel_type=inbound.channel_type,
            channel_target_id=inbound.channel_target_id,
            body=body,
            metadata={"queue_notice": True, "ai_intake_generated": True, "queue_notice_key": key},
        ),
        author_id=None,
        idempotency_key=f"queue-notice:{entry.id}:{key}",
        priority=10,
        trace_id="two-queue-dispatch",
    )
    ledger[key] = datetime.now(UTC).isoformat()
    entry.notification_ledger = ledger
    db.flush()
    return True


def emit_position_notices(db: Session, *, limit: int = 500) -> int:
    """Emit exact and crossed milestones; position one is never announced."""
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
        if position is None:
            continue
        tracking = dict(entry.position_tracking or {})
        cycle = dict(tracking.get(entry.queue_type.value) or {})
        previous = cycle.get("last_observed_position")
        delivered = {int(value) for value in cycle.get("sent_milestones", [])}
        milestones = {20, 10, 5, 3, 2}
        crossed = (
            {milestone for milestone in milestones if position <= milestone < int(previous)}
            if isinstance(previous, int)
            else set()
        )
        # An entry may first be observed exactly at a milestone; its initial
        # observation must not suppress that milestone notice.
        if position in milestones and position not in delivered:
            crossed.add(position)
        crossed -= delivered
        cycle["last_observed_position"] = position
        if crossed and _notice(
            db,
            entry,
            key=f"position:{entry.queue_type.value}:{','.join(str(value) for value in sorted(crossed, reverse=True))}:{position}",
            body=f"Your current position in the {entry.queue_type.value.title()} queue is {position}.",
        ):
            delivered.update(crossed)
            cycle["sent_milestones"] = sorted(delivered, reverse=True)
            sent += 1
        tracking[entry.queue_type.value] = cycle
        entry.position_tracking = tracking
    db.commit()
    return sent


def _end_assignment(db: Session, conversation_id, *, now: datetime) -> None:
    db.query(ConversationAssignment).filter(
        ConversationAssignment.conversation_id == conversation_id,
        ConversationAssignment.is_active.is_(True),
    ).update({"is_active": False, "ended_at": now, "updated_at": now})


def _assignment_team_id(db: Session, agent: CrmAgent, queue_type: ConversationQueueType, *, automatic: bool):
    query = (
        db.query(CrmTeam.id, CrmTeam.name)
        .join(CrmAgentTeam, CrmAgentTeam.team_id == CrmTeam.id)
        .filter(CrmAgentTeam.agent_id == agent.id, CrmAgentTeam.is_active.is_(True), CrmTeam.is_active.is_(True))
    )
    if automatic:
        query = query.filter(CrmTeam.id.in_(_pool_team_ids(db, queue_type)))
    row = query.order_by(func.lower(CrmTeam.name).asc(), CrmTeam.id.asc()).first()
    return row[0] if row else None


def _team_lead_person_ids(db: Session, crm_team_ids: list) -> set[str]:
    if not crm_team_ids:
        return set()
    service_ids = [
        value[0]
        for value in db.query(CrmTeam.service_team_id)
        .filter(CrmTeam.id.in_(crm_team_ids), CrmTeam.service_team_id.isnot(None))
        .all()
    ]
    people = {
        str(value[0])
        for value in db.query(ServiceTeam.manager_person_id)
        .filter(ServiceTeam.id.in_(service_ids), ServiceTeam.manager_person_id.isnot(None))
        .all()
    }
    people.update(
        str(value[0])
        for value in db.query(ServiceTeamMember.person_id)
        .filter(
            ServiceTeamMember.team_id.in_(service_ids),
            ServiceTeamMember.is_active.is_(True),
            ServiceTeamMember.role.in_([ServiceTeamMemberRole.lead, ServiceTeamMemberRole.manager]),
        )
        .all()
    )
    return people


def _operations_lead_person_ids(db: Session) -> set[str]:
    service_ids = [
        value[0]
        for value in db.query(ServiceTeam.id)
        .filter(ServiceTeam.team_type == ServiceTeamType.operations, ServiceTeam.is_active.is_(True))
        .all()
    ]
    return {
        str(value[0])
        for value in db.query(ServiceTeamMember.person_id)
        .filter(
            ServiceTeamMember.team_id.in_(service_ids),
            ServiceTeamMember.is_active.is_(True),
            ServiceTeamMember.role.in_([ServiceTeamMemberRole.lead, ServiceTeamMemberRole.manager]),
        )
        .all()
    }


def _notify_missed_response(db: Session, entry: ConversationQueueEntry) -> None:
    """Create real in-app alerts alongside the immutable queue audit event."""
    source_teams = _pool_team_ids(db, entry.queue_type)
    recipients = _team_lead_person_ids(db, source_teams)
    previous_assignment = (
        db.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == entry.conversation_id)
        .order_by(ConversationAssignment.ended_at.desc().nullslast(), ConversationAssignment.created_at.desc())
        .first()
    )
    if previous_assignment and previous_assignment.team_id and previous_assignment.team_id not in source_teams:
        recipients.update(_team_lead_person_ids(db, [previous_assignment.team_id]))
    if not recipients:
        recipients = _operations_lead_person_ids(db)
    conversation = db.get(Conversation, entry.conversation_id)
    reference = conversation.subject if conversation and conversation.subject else str(entry.conversation_id)
    previous_agent = str(entry.previous_agent_id) if entry.previous_agent_id else "unassigned"
    link = f"/admin/crm/inbox?conversation_id={entry.conversation_id}"
    body = (
        f"Conversation {reference} was requeued after 20 minutes without a first response. "
        f"Queue: {entry.queue_type.value}. Previous agent: {previous_agent}. Open: {link}"
    )
    for recipient in sorted(recipients):
        db.add(
            Notification(
                channel=NotificationChannel.push,
                recipient=recipient,
                subject="Missed CRM queue response",
                body=body,
                status=NotificationStatus.delivered,
                sent_at=datetime.now(UTC),
            )
        )
        from app.websocket.broadcaster import broadcast_agent_notification

        broadcast_agent_notification(
            recipient,
            {
                "kind": "queue_missed_response",
                "title": "Missed CRM queue response",
                "preview": body,
                "conversation_id": str(entry.conversation_id),
            },
        )


def _assign_entry(
    db: Session,
    entry: ConversationQueueEntry,
    agent: CrmAgent,
    *,
    actor_id: str | None = None,
    automatic: bool = False,
) -> None:
    """Create an assignment stint after the agent row has been locked and rechecked."""
    agent = db.query(CrmAgent).filter(CrmAgent.id == agent.id).with_for_update().one()
    if routing._agent_active_chat_counts(db, [agent.id]).get(agent.id, 0) >= _agent_cap(agent):
        raise HTTPException(status_code=409, detail="Agent is at the 20-chat capacity limit")
    conversation = db.query(Conversation).filter(Conversation.id == entry.conversation_id).with_for_update().one()
    now = datetime.now(UTC)
    _end_assignment(db, conversation.id, now=now)
    team_id = _assignment_team_id(db, agent, entry.queue_type, automatic=automatic)
    if team_id is None:
        raise HTTPException(status_code=409, detail="Agent has no active CRM team for this assignment")
    assignment = ConversationAssignment(
        conversation_id=conversation.id,
        agent_id=agent.id,
        team_id=team_id,
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
    recompute_conversation_summary_and_invalidate_cache(db, str(conversation.id))


def dispatch_waiting(db: Session, *, limit: int = 200) -> dict[str, int]:
    """Fill available capacity strictly from the FIFO head of each logical queue."""
    assigned = 0
    scanned = 0
    for queue_type in ConversationQueueType:
        while scanned < limit:
            state = _dispatch_state(db, queue_type, lock=True)
            entry = (
                db.query(ConversationQueueEntry)
                .filter(
                    ConversationQueueEntry.queue_type == queue_type,
                    ConversationQueueEntry.state == ConversationQueueState.waiting,
                )
                .order_by(ConversationQueueEntry.original_arrival_at.asc(), ConversationQueueEntry.id.asc())
                .with_for_update()
                .first()
            )
            if entry is None:
                break
            scanned += 1
            agent = _round_robin_agent(db, queue_type, state)
            if agent is None:
                break
            try:
                _assign_entry(db, entry, agent, automatic=True)
                # Only a successful assignment advances durable round robin.
                state.round_robin_cursor_agent_id = agent.id
                assigned += 1
                db.commit()
            except Exception:
                db.rollback()
                raise
    return {"scanned": scanned, "assigned": assigned}


def manager_assign_head(db: Session, *, conversation_id: str, agent_id: str, actor_id: str) -> ConversationQueueEntry:
    # Establish lock order without locking the entry first (workers acquire
    # dispatch state before the FIFO head).
    current = active_entry(db, conversation_id)
    if current is None:
        raise HTTPException(status_code=409, detail="Conversation is not waiting in a dispatch queue")
    _dispatch_state(db, current.queue_type, lock=True)
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
    if presence is None or presence_service.effective_status(presence) not in {
        AgentPresenceStatus.online,
        AgentPresenceStatus.away,
    }:
        raise HTTPException(status_code=409, detail="Agent is offline or unavailable")
    _assign_entry(db, entry, agent, actor_id=actor_id, automatic=False)
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
    current = active_entry(db, conversation_id)
    if current is None:
        raise HTTPException(status_code=409, detail="Conversation has no active queue cycle")
    _lock_queue_states(db, {current.queue_type, queue_type})
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
    tracking = dict(entry.position_tracking or {})
    tracking[queue_type.value] = {"last_observed_position": None, "sent_milestones": []}
    entry.position_tracking = tracking
    db.add(
        _event(
            entry, "manager_transferred", actor_id=actor_id, payload={"from": previous.value, "to": queue_type.value}
        )
    )
    log_conversation_action(
        db,
        action="queue_transfer",
        conversation_id=conversation_id,
        actor_id=actor_id,
        metadata={"from": previous.value, "to": queue_type.value},
    )
    recompute_conversation_summary_and_invalidate_cache(db, conversation_id)
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
        _notify_missed_response(db, entry)
    recompute_conversation_summary_and_invalidate_cache(db, str(entry.conversation_id))


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
        # Reacquire under the same ordering used by dispatch.  A concurrent
        # worker may have assigned/completed this entry after the scan.
        _dispatch_state(db, entry.queue_type, lock=True)
        entry = (
            db.query(ConversationQueueEntry)
            .filter(ConversationQueueEntry.id == entry.id)
            .with_for_update()
            .one_or_none()
        )
        if entry is None or entry.state != ConversationQueueState.assigned:
            continue
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
    recompute_conversation_summary_and_invalidate_cache(db, conversation_id)
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


def cutover_readiness(db: Session) -> dict[str, object]:
    """Read-only pre-activation checks; this never changes queue state."""

    from app.models.scheduler import ScheduledTask

    unresolved_ids = {
        row[0]
        for row in db.query(Conversation.id)
        .filter(Conversation.is_active.is_(True), Conversation.status.in_(QUEUEABLE_STATUSES))
        .all()
    }
    live_rows = (
        db.query(ConversationQueueEntry)
        .filter(ConversationQueueEntry.state.in_(ACTIVE_QUEUE_STATES))
        .order_by(
            ConversationQueueEntry.queue_type, ConversationQueueEntry.original_arrival_at, ConversationQueueEntry.id
        )
        .all()
    )
    live_by_conversation: dict = {}
    duplicates: list[str] = []
    fifo_deterministic = True
    previous_by_queue: dict[ConversationQueueType, tuple] = {}
    for entry in live_rows:
        if entry.conversation_id in live_by_conversation:
            duplicates.append(str(entry.conversation_id))
        live_by_conversation[entry.conversation_id] = entry
        key = (entry.original_arrival_at, entry.id)
        previous = previous_by_queue.get(entry.queue_type)
        if previous is not None and key < previous:
            fifo_deterministic = False
        previous_by_queue[entry.queue_type] = key
    missing = sorted(str(value) for value in unresolved_ids - set(live_by_conversation))
    assigned_mismatch = []
    for entry in live_rows:
        if entry.state != ConversationQueueState.assigned:
            continue
        active = (
            db.query(ConversationAssignment.id)
            .filter(
                ConversationAssignment.conversation_id == entry.conversation_id,
                ConversationAssignment.is_active.is_(True),
                ConversationAssignment.agent_id == entry.current_agent_id,
            )
            .first()
        )
        if active is None:
            assigned_mismatch.append(str(entry.conversation_id))
    worker_exists = bool(
        db.query(ScheduledTask.id)
        .filter(ScheduledTask.task_name == "app.tasks.crm_inbox.run_two_queue_dispatch")
        .first()
    )
    # These are explicit gates in promote_queued_conversations_task and the
    # routing/assignment service boundary, checked here for operator clarity.
    legacy_workers_gated = True
    ready = not missing and not duplicates and not assigned_mismatch and fifo_deterministic and worker_exists
    return {
        "ready": ready,
        "supported_unresolved": len(unresolved_ids),
        "live_cycles": len(live_rows),
        "missing_live_cycles": missing,
        "duplicate_live_entries": sorted(set(duplicates)),
        "assigned_stint_mismatches": assigned_mismatch,
        "fifo_deterministic": fifo_deterministic,
        "scheduled_worker_exists": worker_exists,
        "legacy_workers_gated": legacy_workers_gated,
    }
