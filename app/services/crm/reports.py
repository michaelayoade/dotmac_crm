from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.orm import Session, joinedload

from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.enums import AgentPresenceStatus, ChannelType, ConversationStatus, MessageDirection
from app.models.crm.presence import AgentPresenceEvent
from app.models.crm.sales import Lead, PipelineStage
from app.models.crm.team import CrmAgent, CrmAgentTeam
from app.models.person import Person
from app.models.projects import Project, ProjectTask
from app.models.tickets import Ticket, TicketSlaEvent, TicketStatus
from app.models.workforce import WorkOrder
from app.services.common import coerce_uuid
from app.services.crm.metrics import ensure_aware
from app.services.crm.metrics import is_resolved_closing_message as _is_resolved_closing_message


def _status_group(status: TicketStatus) -> str:
    if status in {TicketStatus.closed, TicketStatus.canceled}:
        return "closed"
    return "open"


def _agent_person_ids(
    db: Session,
    agent_id: str | None,
    team_id: str | None,
) -> list[str] | None:
    if agent_id:
        agent = db.get(CrmAgent, coerce_uuid(agent_id))
        if not agent:
            return []
        return [str(agent.person_id)]
    if team_id:
        agents = (
            db.query(CrmAgent)
            .join(CrmAgentTeam, CrmAgentTeam.agent_id == CrmAgent.id)
            .filter(CrmAgentTeam.team_id == coerce_uuid(team_id))
            .filter(CrmAgentTeam.is_active.is_(True))
            .all()
        )
        return [str(agent.person_id) for agent in agents]
    return None


def _message_activity_time():
    return func.coalesce(Message.received_at, Message.sent_at, Message.created_at)


def _resolve_channel_value(channel_type: str | None) -> ChannelType | None:
    if not channel_type:
        return None
    try:
        return ChannelType(channel_type)
    except ValueError:
        return None


def _resolution_timestamp(conversation: Conversation) -> datetime | None:
    return conversation.resolved_at or (
        conversation.updated_at if conversation.status == ConversationStatus.resolved else None
    )


def _is_resolved_in_window(conversation: Conversation, start_at: datetime | None, end_at: datetime | None) -> bool:
    if conversation.status != ConversationStatus.resolved:
        return False
    resolved_at = _resolution_timestamp(conversation)
    if not resolved_at:
        return False
    if resolved_at.tzinfo is None:
        resolved_at = resolved_at.replace(tzinfo=UTC)
    if start_at and start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=UTC)
    if end_at and end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=UTC)
    if start_at and resolved_at < start_at:
        return False
    return not (end_at and resolved_at > end_at)


def agent_presence_summary(
    db: Session,
    *,
    start_at: datetime,
    end_at: datetime,
    agent_id: str,
) -> dict[str, float]:
    """Return presence duration hours by status for a single agent over a time window.

    Uses crm_agent_presence_events. Events are clipped to [start_at, end_at].
    """
    start = start_at
    end = end_at
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    if end <= start:
        return {s.value: 0.0 for s in AgentPresenceStatus}

    agent_uuid = coerce_uuid(agent_id)
    rows = (
        db.query(AgentPresenceEvent)
        .filter(AgentPresenceEvent.agent_id == agent_uuid)
        .filter(AgentPresenceEvent.started_at < end)
        .filter(func.coalesce(AgentPresenceEvent.ended_at, end) > start)
        .order_by(AgentPresenceEvent.started_at.asc())
        .all()
    )

    seconds_by_status: dict[str, float] = {s.value: 0.0 for s in AgentPresenceStatus}
    for ev in rows:
        ev_start = ev.started_at
        ev_end = ev.ended_at or end
        if ev_start.tzinfo is None:
            ev_start = ev_start.replace(tzinfo=UTC)
        if ev_end.tzinfo is None:
            ev_end = ev_end.replace(tzinfo=UTC)
        overlap_start = max(start, ev_start)
        overlap_end = min(end, ev_end)
        if overlap_end <= overlap_start:
            continue
        seconds_by_status[ev.status.value] = (
            seconds_by_status.get(ev.status.value, 0.0) + (overlap_end - overlap_start).total_seconds()
        )

    # Convert to hours for display.
    return {k: round(v / 3600.0, 2) for k, v in seconds_by_status.items()}


def ticket_support_metrics(
    db: Session,
    start_at: datetime | None,
    end_at: datetime | None,
    agent_id: str | None,
    team_id: str | None,
) -> dict:
    query = db.query(Ticket)
    if start_at:
        query = query.filter(Ticket.created_at >= start_at)
    if end_at:
        query = query.filter(Ticket.created_at <= end_at)
    person_ids = _agent_person_ids(db, agent_id, team_id)
    if person_ids is not None:
        if not person_ids:
            return {
                "tickets": {"total": 0, "open": 0, "closed": 0},
                "avg_resolution_hours": None,
                "sla": {
                    "total": 0,
                    "met": 0,
                    "breached": 0,
                    "by_event_type": {},
                    "compliance_percent": None,
                },
            }
        query = query.filter(Ticket.assigned_to_person_id.in_([coerce_uuid(pid) for pid in person_ids]))

    tickets = query.all()
    totals = {"total": len(tickets), "open": 0, "closed": 0}
    resolution_hours = []
    for ticket in tickets:
        totals[_status_group(ticket.status)] += 1
        if ticket.resolved_at:
            delta = ticket.resolved_at - ticket.created_at
            resolution_hours.append(delta.total_seconds() / 3600)

    avg_resolution = sum(resolution_hours) / len(resolution_hours) if resolution_hours else None

    sla_query = db.query(TicketSlaEvent).join(Ticket)
    if start_at:
        sla_query = sla_query.filter(TicketSlaEvent.created_at >= start_at)
    if end_at:
        sla_query = sla_query.filter(TicketSlaEvent.created_at <= end_at)
    if person_ids is not None:
        if not person_ids:
            sla_query = sla_query.filter(TicketSlaEvent.id == None)  # noqa: E711
        else:
            sla_query = sla_query.filter(Ticket.assigned_to_person_id.in_([coerce_uuid(pid) for pid in person_ids]))
    sla_events = sla_query.all()
    sla_totals: dict[str, Any] = {
        "total": 0,
        "met": 0,
        "breached": 0,
        "by_event_type": {},
        "by_priority": {},
    }
    for event in sla_events:
        if not event.expected_at or not event.actual_at:
            continue
        sla_totals["total"] += 1
        met = event.actual_at <= event.expected_at
        if met:
            sla_totals["met"] += 1
        else:
            sla_totals["breached"] += 1
        bucket = sla_totals["by_event_type"].setdefault(event.event_type, {"total": 0, "met": 0, "breached": 0})
        bucket["total"] += 1
        if met:
            bucket["met"] += 1
        else:
            bucket["breached"] += 1
        if event.ticket and event.ticket.priority:
            priority_key = event.ticket.priority.value
            priority_bucket = sla_totals["by_priority"].setdefault(priority_key, {"total": 0, "met": 0, "breached": 0})
            priority_bucket["total"] += 1
            if met:
                priority_bucket["met"] += 1
            else:
                priority_bucket["breached"] += 1

    compliance = None
    if sla_totals["total"] > 0:
        compliance = Decimal(sla_totals["met"]) / Decimal(sla_totals["total"]) * Decimal("100")

    return {
        "tickets": totals,
        "avg_resolution_hours": avg_resolution,
        "sla": {
            **sla_totals,
            "compliance_percent": float(compliance) if compliance is not None else None,
        },
    }


def inbox_kpis(
    db: Session,
    start_at: datetime | None,
    end_at: datetime | None,
    channel_type: str | None,
    agent_id: str | None,
    team_id: str | None,
) -> dict:
    from app.models.integration import IntegrationTarget

    message_query = db.query(Message)
    message_time = _message_activity_time()
    if start_at:
        message_query = message_query.filter(message_time >= start_at)
    if end_at:
        message_query = message_query.filter(message_time <= end_at)
    channel_value = _resolve_channel_value(channel_type)
    if channel_value:
        message_query = message_query.filter(Message.channel_type == channel_value)

    conversation_ids = None
    if agent_id or team_id:
        assignment_query = db.query(ConversationAssignment.conversation_id).filter(
            ConversationAssignment.is_active.is_(True)
        )
        if agent_id:
            assignment_query = assignment_query.filter(ConversationAssignment.agent_id == coerce_uuid(agent_id))
        if team_id:
            assignment_query = assignment_query.filter(ConversationAssignment.team_id == coerce_uuid(team_id))
        conversation_ids = [row[0] for row in assignment_query.all()]
        if not conversation_ids:
            return {
                "messages": {"total": 0, "inbound": 0, "outbound": 0, "by_channel": {}},
                "avg_response_minutes": None,
                "avg_resolution_minutes": None,
            }
        message_query = message_query.filter(Message.conversation_id.in_(conversation_ids))

    total_messages = message_query.count()
    inbound_messages = message_query.filter(Message.direction == MessageDirection.inbound).count()
    outbound_messages = message_query.filter(Message.direction == MessageDirection.outbound).count()

    active_conversation_ids = [row[0] for row in message_query.with_entities(Message.conversation_id).distinct().all()]
    conversations = (
        db.query(Conversation).filter(Conversation.id.in_(active_conversation_ids)).all()
        if active_conversation_ids
        else []
    )

    response_times = []
    resolution_times = []

    if conversations:
        convo_ids = [c.id for c in conversations]

        # Batch query: first inbound message per conversation using window function

        inbound_subq = (
            db.query(
                Message.conversation_id,
                message_time.label("msg_time"),
                func.row_number()
                .over(
                    partition_by=Message.conversation_id,
                    order_by=message_time.asc(),
                )
                .label("rn"),
            )
            .filter(Message.conversation_id.in_(convo_ids))
            .filter(Message.direction == MessageDirection.inbound)
        )
        if start_at:
            inbound_subq = inbound_subq.filter(message_time >= start_at)
        if end_at:
            inbound_subq = inbound_subq.filter(message_time <= end_at)
        if channel_value:
            inbound_subq = inbound_subq.filter(Message.channel_type == channel_value)
        inbound_subq = inbound_subq.subquery()
        first_inbound = (
            db.query(inbound_subq.c.conversation_id, inbound_subq.c.msg_time).filter(inbound_subq.c.rn == 1).all()
        )
        inbound_map = {row[0]: row[1] for row in first_inbound}

        # Batch query: first outbound message per conversation
        outbound_subq = (
            db.query(
                Message.conversation_id,
                message_time.label("msg_time"),
                func.row_number()
                .over(
                    partition_by=Message.conversation_id,
                    order_by=message_time.asc(),
                )
                .label("rn"),
            )
            .filter(Message.conversation_id.in_(convo_ids))
            .filter(Message.direction == MessageDirection.outbound)
        )
        if start_at:
            outbound_subq = outbound_subq.filter(message_time >= start_at)
        if end_at:
            outbound_subq = outbound_subq.filter(message_time <= end_at)
        if channel_value:
            outbound_subq = outbound_subq.filter(Message.channel_type == channel_value)
        outbound_subq = outbound_subq.subquery()
        first_outbound = (
            db.query(outbound_subq.c.conversation_id, outbound_subq.c.msg_time).filter(outbound_subq.c.rn == 1).all()
        )
        outbound_map = {row[0]: row[1] for row in first_outbound}

        # Calculate response and resolution times from batch results
        for convo in conversations:
            inbound_time = inbound_map.get(convo.id)
            outbound_time = outbound_map.get(convo.id)
            if inbound_time and outbound_time and outbound_time > inbound_time:
                response_times.append((outbound_time - inbound_time).total_seconds() / 60)
            resolved_at = _resolution_timestamp(convo)
            if _is_resolved_in_window(convo, start_at, end_at) and inbound_time and resolved_at:
                resolution_times.append((resolved_at - inbound_time).total_seconds() / 60)

    avg_response_minutes = sum(response_times) / len(response_times) if response_times else None
    avg_resolution_minutes = sum(resolution_times) / len(resolution_times) if resolution_times else None

    channel_volume = (
        message_query.with_entities(Message.channel_type, func.count(Message.id)).group_by(Message.channel_type).all()
    )
    channel_volume_map = {str(channel): count for channel, count in channel_volume}

    email_inbox_rows = (
        message_query.filter(Message.channel_type == ChannelType.email)
        .outerjoin(IntegrationTarget, IntegrationTarget.id == Message.channel_target_id)
        .with_entities(Message.channel_target_id, IntegrationTarget.name, func.count(Message.id))
        .group_by(Message.channel_target_id, IntegrationTarget.name)
        .all()
    )
    email_inbox_map: dict[str, dict[str, Any]] = {}
    for inbox_id, inbox_name, count in email_inbox_rows:
        inbox_key = str(inbox_id) if inbox_id else "none"
        label = inbox_name or "Unknown Inbox"
        email_inbox_map[inbox_key] = {"label": label, "count": int(count or 0)}

    return {
        "messages": {
            "total": total_messages,
            "inbound": inbound_messages,
            "outbound": outbound_messages,
            "by_channel": channel_volume_map,
            "by_email_inbox": email_inbox_map,
        },
        "conversations": {
            "active": len(conversations),
            "resolved": sum(1 for convo in conversations if _is_resolved_in_window(convo, start_at, end_at)),
        },
        "avg_response_minutes": avg_response_minutes,
        "avg_resolution_minutes": avg_resolution_minutes,
    }


def pipeline_stage_metrics(
    db: Session,
    pipeline_id: str,
) -> dict:
    pipeline_uuid = coerce_uuid(pipeline_id)
    stages = (
        db.query(PipelineStage)
        .filter(PipelineStage.pipeline_id == pipeline_uuid)
        .filter(PipelineStage.is_active.is_(True))
        .order_by(PipelineStage.order_index.asc())
        .all()
    )
    leads = db.query(Lead).filter(Lead.pipeline_id == pipeline_uuid).all()
    total = len(leads)
    won = len([lead for lead in leads if lead.status.value == "won"])
    lost = len([lead for lead in leads if lead.status.value == "lost"])
    stage_counts = {}
    for stage in stages:
        stage_counts[str(stage.id)] = {
            "name": stage.name,
            "count": len([lead for lead in leads if lead.stage_id == stage.id]),
        }
    conversion_rate = (won / total * 100) if total else None
    return {
        "total_leads": total,
        "won": won,
        "lost": lost,
        "conversion_percent": conversion_rate,
        "stages": stage_counts,
    }


def field_service_metrics(
    db: Session,
    start_at: datetime | None,
    end_at: datetime | None,
    agent_id: str | None,
    team_id: str | None,
) -> dict:
    person_ids = _agent_person_ids(db, agent_id, team_id)
    query = db.query(WorkOrder)
    if start_at:
        query = query.filter(WorkOrder.created_at >= start_at)
    if end_at:
        query = query.filter(WorkOrder.created_at <= end_at)
    if person_ids is not None:
        if not person_ids:
            return {"total": 0, "status": {}, "avg_completion_hours": None}
        query = query.filter(WorkOrder.assigned_to_person_id.in_([coerce_uuid(pid) for pid in person_ids]))
    work_orders = query.all()
    status_counts: dict[str, int] = {}
    completion_hours: list[float] = []
    for order in work_orders:
        key = order.status.value if order.status else "unknown"
        status_counts[key] = status_counts.get(key, 0) + 1
        if order.completed_at and order.started_at:
            completion_hours.append((order.completed_at - order.started_at).total_seconds() / 3600)
    avg_completion_hours = sum(completion_hours) / len(completion_hours) if completion_hours else None
    return {
        "total": len(work_orders),
        "status": status_counts,
        "avg_completion_hours": avg_completion_hours,
    }


def project_metrics(
    db: Session,
    start_at: datetime | None,
    end_at: datetime | None,
    agent_id: str | None,
    team_id: str | None,
) -> dict:
    person_ids = _agent_person_ids(db, agent_id, team_id)
    project_query = db.query(Project)
    if start_at:
        project_query = project_query.filter(Project.created_at >= start_at)
    if end_at:
        project_query = project_query.filter(Project.created_at <= end_at)
    if person_ids is not None:
        if not person_ids:
            return {"projects": {"total": 0, "status": {}}, "tasks": {"total": 0, "status": {}}}
        project_query = project_query.filter(
            (Project.owner_person_id.in_([coerce_uuid(pid) for pid in person_ids]))
            | (Project.manager_person_id.in_([coerce_uuid(pid) for pid in person_ids]))
        )
    projects = project_query.all()
    project_status: dict[str, int] = {}
    for project in projects:
        key = project.status.value if project.status else "unknown"
        project_status[key] = project_status.get(key, 0) + 1

    task_query = db.query(ProjectTask)
    if start_at:
        task_query = task_query.filter(ProjectTask.created_at >= start_at)
    if end_at:
        task_query = task_query.filter(ProjectTask.created_at <= end_at)
    if person_ids is not None:
        if not person_ids:
            tasks = []
        else:
            task_query = task_query.filter(
                ProjectTask.assigned_to_person_id.in_([coerce_uuid(pid) for pid in person_ids])
            )
    tasks = task_query.all()
    task_status: dict[str, int] = {}
    for task in tasks:
        key = task.status.value if task.status else "unknown"
        task_status[key] = task_status.get(key, 0) + 1

    return {
        "projects": {"total": len(projects), "status": project_status},
        "tasks": {"total": len(tasks), "status": task_status},
    }


def agent_performance_metrics(
    db: Session,
    start_at: datetime | None,
    end_at: datetime | None,
    agent_id: str | None,
    team_id: str | None,
    channel_type: str | None,
) -> list[dict]:
    """Get per-agent metrics from assignment stints started in the period.

    Agent FRT is always the stint's assigned_at to that same agent's first
    qualifying reply. Customer and AI time before assignment is not included.
    """
    from app.models.person import Person

    agents_query = db.query(CrmAgent).filter(CrmAgent.is_active.is_(True))
    if agent_id:
        agents_query = agents_query.filter(CrmAgent.id == coerce_uuid(agent_id))
    if team_id:
        agents_query = (
            agents_query.join(CrmAgentTeam, CrmAgentTeam.agent_id == CrmAgent.id)
            .filter(CrmAgentTeam.team_id == coerce_uuid(team_id))
            .filter(CrmAgentTeam.is_active.is_(True))
        )
    agents = agents_query.limit(100).all()
    if not agents:
        return []

    person_ids = [agent.person_id for agent in agents if agent.person_id]
    persons = db.query(Person).filter(Person.id.in_(person_ids)).all() if person_ids else []
    person_map = {person.id: person for person in persons}

    presence_seconds_by_agent: dict[str, dict[str, float]] = {}
    if start_at and end_at:
        from app.services.crm.presence import agent_presence

        presence_seconds_by_agent = agent_presence.seconds_by_status_bulk(
            db,
            agent_ids=[str(a.id) for a in agents],
            start_at=start_at,
            end_at=end_at,
        )

    # Assignment start is the report cohort and the authoritative FRT start.
    agent_ids = [agent.id for agent in agents]
    assignment_query = (
        db.query(ConversationAssignment)
        .filter(ConversationAssignment.agent_id.in_(agent_ids))
        .filter(ConversationAssignment.assigned_at.isnot(None))
    )
    if start_at:
        assignment_query = assignment_query.filter(ConversationAssignment.assigned_at >= start_at)
    if end_at:
        assignment_query = assignment_query.filter(ConversationAssignment.assigned_at <= end_at)
    assignments = assignment_query.order_by(ConversationAssignment.assigned_at.asc()).all()
    assignments_by_agent: dict[Any, list[ConversationAssignment]] = {agent.id: [] for agent in agents}
    for assignment in assignments:
        if assignment.agent_id is not None:
            assignments_by_agent.setdefault(assignment.agent_id, []).append(assignment)

    channel_value = _resolve_channel_value(channel_type)
    all_conversation_ids = {assignment.conversation_id for assignment in assignments}
    conversations = (
        db.query(Conversation).filter(Conversation.id.in_(all_conversation_ids)).all() if all_conversation_ids else []
    )
    conversations_by_id = {conversation.id: conversation for conversation in conversations}

    # Channel activity determines which assignment stints belong to a filtered
    # report. Outbound candidates are also the safe fallback for pre-migration
    # rows whose per-stint result has not yet been captured.
    channel_conversation_ids: set[Any] = set(all_conversation_ids) if not channel_value else set()
    outbound_candidates: dict[tuple[str, str], list[tuple[Any, Message]]] = {}
    if all_conversation_ids:
        message_time = _message_activity_time()
        if channel_value:
            channel_conversation_ids = {
                row[0]
                for row in db.query(Message.conversation_id)
                .filter(Message.conversation_id.in_(all_conversation_ids))
                .filter(Message.channel_type == channel_value)
                .distinct()
                .all()
            }

        outbound_rows = (
            db.query(Message.conversation_id, Message.author_id, message_time.label("msg_time"), Message)
            .filter(Message.conversation_id.in_(all_conversation_ids))
            .filter(Message.direction == MessageDirection.outbound)
            .filter(Message.author_id.isnot(None))
            .filter(Message.author_id.in_(person_ids))
        )
        if end_at:
            outbound_rows = outbound_rows.filter(message_time <= end_at)
        if channel_value:
            outbound_rows = outbound_rows.filter(Message.channel_type == channel_value)

        for conversation_id, author_id, msg_time, message in outbound_rows.order_by(message_time.asc()).all():
            metadata_map = message.metadata_ if isinstance(message.metadata_, dict) else {}
            if metadata_map.get("ai_intake_generated"):
                continue
            conversation = conversations_by_id.get(conversation_id)
            if conversation is not None and _is_resolved_closing_message(message, conversation=conversation):
                continue
            outbound_candidates.setdefault((str(conversation_id), str(author_id)), []).append((msg_time, message))

    agent_stats: list[dict[str, Any]] = []
    for agent in agents:
        by_status = presence_seconds_by_agent.get(str(agent.id), {})
        active_seconds = float(by_status.get("online", 0.0) + by_status.get("away", 0.0))
        active_hours = active_seconds / 3600.0 if active_seconds else 0.0
        active_hours_display: str | None
        if start_at and end_at:
            total_minutes = round(active_seconds / 60.0)
            hours = total_minutes // 60
            minutes = total_minutes % 60
            active_hours_display = f"{hours}h {minutes:02d}m"
        else:
            active_hours_display = None

        agent_assignments = [
            assignment
            for assignment in assignments_by_agent.get(agent.id, [])
            if assignment.conversation_id in channel_conversation_ids
        ]
        conversation_ids = {assignment.conversation_id for assignment in agent_assignments}
        agent_conversations = [
            conversations_by_id[conversation_id]
            for conversation_id in conversation_ids
            if conversation_id in conversations_by_id
        ]
        person = person_map.get(agent.person_id)

        total = len(agent_conversations)
        total_assignments = len(agent_assignments)
        resolved = sum(
            1 for conversation in agent_conversations if _is_resolved_in_window(conversation, start_at, end_at)
        )

        response_times: list[float] = []
        resolution_times: list[float] = []
        latest_assignment_by_conversation: dict[Any, ConversationAssignment] = {}
        for assignment in agent_assignments:
            assignment_start = ensure_aware(assignment.assigned_at) or ensure_aware(assignment.created_at)
            assignment_end = ensure_aware(assignment.ended_at)
            if assignment_start is None:
                continue
            latest_assignment_by_conversation[assignment.conversation_id] = assignment

            response_at = ensure_aware(assignment.first_response_at)
            if (
                response_at is not None
                and assignment.response_time_seconds is not None
                and response_at >= assignment_start
                and (assignment_end is None or response_at < assignment_end)
                and (end_at is None or response_at <= (ensure_aware(end_at) or end_at))
            ):
                response_times.append(assignment.response_time_seconds / 60)
                continue

            for outbound_time, _message in outbound_candidates.get(
                (str(assignment.conversation_id), str(agent.person_id)), []
            ):
                outbound_at = ensure_aware(outbound_time)
                if outbound_at is None or outbound_at < assignment_start:
                    continue
                if assignment_end is not None and outbound_at >= assignment_end:
                    continue
                response_times.append((outbound_at - assignment_start).total_seconds() / 60)
                break

        for conversation_id, assignment in latest_assignment_by_conversation.items():
            conversation = conversations_by_id.get(conversation_id)
            if conversation is None or not _is_resolved_in_window(conversation, start_at, end_at):
                continue
            resolved_at = ensure_aware(_resolution_timestamp(conversation))
            assignment_start = ensure_aware(assignment.assigned_at) or ensure_aware(assignment.created_at)
            assignment_end = ensure_aware(assignment.ended_at)
            if (
                resolved_at is not None
                and assignment_start is not None
                and resolved_at >= assignment_start
                and (assignment_end is None or resolved_at <= assignment_end)
            ):
                resolution_times.append((resolved_at - assignment_start).total_seconds() / 60)

        avg_frt = sum(response_times) / len(response_times) if response_times else None
        avg_resolution = sum(resolution_times) / len(resolution_times) if resolution_times else None

        name = (
            (person.display_name or f"{person.first_name or ''} {person.last_name or ''}".strip() or "Agent")
            if person
            else "Agent"
        )

        agent_stats.append(
            {
                "agent_id": str(agent.id),
                "name": name,
                "total_conversations": total,
                "total_assignments": total_assignments,
                "resolved_conversations": resolved,
                "avg_first_response_minutes": round(avg_frt, 1) if avg_frt is not None else None,
                "avg_resolution_minutes": round(avg_resolution, 1) if avg_resolution is not None else None,
                "first_response_count": len(response_times),
                "unanswered_assignments": max(0, total_assignments - len(response_times)),
                "response_coverage_percent": (
                    round(len(response_times) / total_assignments * 100, 1) if total_assignments else 0.0
                ),
                "resolution_time_count": len(resolution_times),
                "active_seconds": int(active_seconds),
                "active_hours": round(active_hours, 2),
                "active_hours_display": active_hours_display,
            }
        )

    agent_stats.sort(
        key=lambda x: int(x.get("resolved_conversations") or 0),
        reverse=True,
    )
    return agent_stats


def conversation_trend(
    db: Session,
    start_at: datetime,
    end_at: datetime,
    agent_id: str | None,
    team_id: str | None,
    channel_type: str | None,
) -> list[dict]:
    """Get daily conversation counts for trend chart.

    Returns a list of {date, total, resolved} for each day in the range.
    """
    from datetime import timedelta

    # Get conversation IDs filtered by agent/team if specified
    conversation_ids = None
    if agent_id or team_id:
        assignment_query = db.query(ConversationAssignment.conversation_id).filter(
            ConversationAssignment.is_active.is_(True)
        )
        if agent_id:
            assignment_query = assignment_query.filter(ConversationAssignment.agent_id == coerce_uuid(agent_id))
        if team_id:
            assignment_query = assignment_query.filter(ConversationAssignment.team_id == coerce_uuid(team_id))
        conversation_ids = [row[0] for row in assignment_query.all()]
    channel_value = _resolve_channel_value(channel_type)

    trend_data = []
    current_date = start_at.date()
    end_date = end_at.date()

    while current_date <= end_date:
        day_start = datetime.combine(current_date, datetime.min.time()).replace(tzinfo=start_at.tzinfo)
        day_end = day_start + timedelta(days=1)

        message_time = _message_activity_time()
        activity_query = db.query(Message.conversation_id).filter(
            message_time >= day_start,
            message_time < day_end,
        )
        if conversation_ids is not None:
            if not conversation_ids:
                trend_data.append(
                    {
                        "date": current_date.strftime("%Y-%m-%d"),
                        "total": 0,
                        "resolved": 0,
                    }
                )
                current_date += timedelta(days=1)
                continue
            activity_query = activity_query.filter(Message.conversation_id.in_(conversation_ids))
        if channel_value:
            activity_query = activity_query.filter(Message.channel_type == channel_value)

        active_conversation_ids = [row[0] for row in activity_query.distinct().all()]
        conversations = (
            db.query(Conversation).filter(Conversation.id.in_(active_conversation_ids)).all()
            if active_conversation_ids
            else []
        )
        total = len(conversations)
        resolved = sum(1 for c in conversations if _is_resolved_in_window(c, day_start, day_end))

        trend_data.append(
            {
                "date": current_date.strftime("%Y-%m-%d"),
                "total": total,
                "resolved": resolved,
            }
        )
        current_date += timedelta(days=1)

    return trend_data


def sales_pipeline_metrics(
    db: Session,
    pipeline_id: str | None,
    start_at: datetime | None,
    end_at: datetime | None,
    owner_agent_id: str | None,
) -> dict:
    """Get sales pipeline metrics including totals, weighted values, and stage breakdown.

    Returns:
        dict with pipeline_value, weighted_value, deal counts, win_rate, avg_deal_size, stages.
    """
    from app.models.crm.enums import LeadStatus

    open_statuses = [
        LeadStatus.new,
        LeadStatus.contacted,
        LeadStatus.qualified,
        LeadStatus.proposal,
        LeadStatus.negotiation,
    ]
    open_query = (
        db.query(Lead)
        .options(joinedload(Lead.stage))
        .filter(Lead.is_active.is_(True))
        .filter(Lead.status.in_(open_statuses))
    )

    if pipeline_id:
        open_query = open_query.filter(Lead.pipeline_id == coerce_uuid(pipeline_id))
    if owner_agent_id:
        open_query = open_query.filter(Lead.owner_agent_id == coerce_uuid(owner_agent_id))
    if start_at:
        open_query = open_query.filter(Lead.created_at >= start_at)
    if end_at:
        open_query = open_query.filter(Lead.created_at <= end_at)

    open_leads = open_query.all()

    total_value = Decimal("0.00")
    weighted_value = Decimal("0.00")
    open_deals = len(open_leads)

    for lead in open_leads:
        if lead.estimated_value:
            total_value += lead.estimated_value
            probability = lead.probability
            if probability is None and lead.stage:
                probability = lead.stage.default_probability
            if probability is not None:
                weighted_value += lead.estimated_value * Decimal(probability) / Decimal(100)

    closed_query = (
        db.query(
            func.sum(case((Lead.status == LeadStatus.won, 1), else_=0)).label("won_count"),
            func.sum(case((Lead.status == LeadStatus.lost, 1), else_=0)).label("lost_count"),
            func.sum(case((Lead.status == LeadStatus.won, Lead.estimated_value), else_=0)).label("won_value"),
        )
        .filter(Lead.is_active.is_(True))
        .filter(Lead.status.in_([LeadStatus.won, LeadStatus.lost]))
        .filter(Lead.closed_at.isnot(None))
    )

    if pipeline_id:
        closed_query = closed_query.filter(Lead.pipeline_id == coerce_uuid(pipeline_id))
    if owner_agent_id:
        closed_query = closed_query.filter(Lead.owner_agent_id == coerce_uuid(owner_agent_id))
    if start_at:
        closed_query = closed_query.filter(Lead.closed_at >= start_at)
    if end_at:
        closed_query = closed_query.filter(Lead.closed_at <= end_at)

    closed_row = closed_query.first()
    won_deals = int(closed_row.won_count or 0) if closed_row else 0
    lost_deals = int(closed_row.lost_count or 0) if closed_row else 0
    won_value = Decimal(closed_row.won_value or 0) if closed_row else Decimal("0.00")

    total_closed = won_deals + lost_deals
    win_rate = (won_deals / total_closed * 100) if total_closed > 0 else None
    avg_deal_size = (won_value / Decimal(won_deals)) if won_deals > 0 else None

    # Get stage breakdown (open leads only, no period filter)
    stages_query = db.query(PipelineStage).filter(PipelineStage.is_active.is_(True))
    if pipeline_id:
        stages_query = stages_query.filter(PipelineStage.pipeline_id == coerce_uuid(pipeline_id))
    stages = stages_query.order_by(PipelineStage.order_index.asc()).all()

    stage_totals: dict[str, dict[str, Decimal | int]] = {}
    for stage in stages:
        stage_totals[str(stage.id)] = {"count": 0, "value": Decimal("0.00")}

    for lead in open_leads:
        if not lead.stage_id:
            continue
        key = str(lead.stage_id)
        if key not in stage_totals:
            continue
        stage_totals[key]["count"] = int(stage_totals[key]["count"]) + 1
        stage_totals[key]["value"] = Decimal(stage_totals[key]["value"]) + Decimal(lead.estimated_value or 0)

    stage_breakdown = []
    for stage in stages:
        totals = stage_totals.get(str(stage.id), {"count": 0, "value": Decimal("0.00")})
        stage_breakdown.append(
            {
                "id": str(stage.id),
                "name": stage.name,
                "count": int(totals["count"]),
                "value": float(totals["value"]),
            }
        )

    return {
        "pipeline_value": float(total_value),
        "weighted_value": float(weighted_value),
        "open_deals": open_deals,
        "won_deals": won_deals,
        "lost_deals": lost_deals,
        "win_rate": round(win_rate, 1) if win_rate is not None else None,
        "avg_deal_size": float(avg_deal_size) if avg_deal_size is not None else None,
        "stages": stage_breakdown,
    }


def sales_forecast(
    db: Session,
    pipeline_id: str | None,
    months_ahead: int = 6,
) -> list[dict]:
    """Get monthly sales forecast based on expected_close_date and weighted_value.

    Returns:
        list of {month, expected_value, weighted_value, deal_count} for each month.
    """
    from calendar import monthrange

    query = db.query(Lead).filter(
        Lead.is_active.is_(True),
        Lead.expected_close_date.isnot(None),
    )

    # Only include leads that aren't won or lost
    from app.models.crm.enums import LeadStatus

    query = query.filter(~Lead.status.in_([LeadStatus.won, LeadStatus.lost]))

    if pipeline_id:
        query = query.filter(Lead.pipeline_id == coerce_uuid(pipeline_id))

    leads = query.options(joinedload(Lead.stage)).all()

    today = datetime.now().date()
    forecast = []

    for i in range(months_ahead):
        # Calculate month start and end
        month_offset = (today.month - 1 + i) % 12 + 1
        year_offset = today.year + ((today.month - 1 + i) // 12)
        month_start = today.replace(year=year_offset, month=month_offset, day=1)
        _, last_day = monthrange(year_offset, month_offset)
        month_end = month_start.replace(day=last_day)

        month_leads = [
            lead for lead in leads if lead.expected_close_date and month_start <= lead.expected_close_date <= month_end
        ]

        expected_value = sum((lead.estimated_value or Decimal(0)) for lead in month_leads)
        weighted_value = Decimal("0.00")
        for lead in month_leads:
            probability = lead.probability
            if probability is None and lead.stage:
                probability = lead.stage.default_probability
            if probability is None:
                continue
            weighted_value += (lead.estimated_value or Decimal(0)) * Decimal(probability) / Decimal(100)

        forecast.append(
            {
                "month": month_start.strftime("%Y-%m"),
                "month_label": month_start.strftime("%b %Y"),
                "expected_value": float(expected_value),
                "weighted_value": float(weighted_value),
                "deal_count": len(month_leads),
            }
        )

    return forecast


def agent_sales_performance(
    db: Session,
    start_at: datetime | None,
    end_at: datetime | None,
    pipeline_id: str | None,
) -> list[dict]:
    """Get per-agent sales performance metrics.

    Returns:
        list of agent stats with deals_won, deals_lost, total_value, win_rate.
    """
    from app.models.crm.enums import LeadStatus

    closed_query = (
        db.query(
            Lead.owner_agent_id.label("agent_id"),
            func.sum(case((Lead.status == LeadStatus.won, 1), else_=0)).label("deals_won"),
            func.sum(case((Lead.status == LeadStatus.lost, 1), else_=0)).label("deals_lost"),
            func.sum(case((Lead.status == LeadStatus.won, Lead.estimated_value), else_=0)).label("won_value"),
            func.count(Lead.id).label("total_closed"),
        )
        .filter(Lead.is_active.is_(True))
        .filter(Lead.owner_agent_id.isnot(None))
        .filter(Lead.status.in_([LeadStatus.won, LeadStatus.lost]))
        .filter(Lead.closed_at.isnot(None))
    )

    if start_at:
        closed_query = closed_query.filter(Lead.closed_at >= start_at)
    if end_at:
        closed_query = closed_query.filter(Lead.closed_at <= end_at)
    if pipeline_id:
        closed_query = closed_query.filter(Lead.pipeline_id == coerce_uuid(pipeline_id))

    closed_rows = closed_query.group_by(Lead.owner_agent_id).all()
    closed_map: dict[str, dict[str, Any]] = {}
    for row in closed_rows:
        agent_id = str(row.agent_id)
        won_value = Decimal(row.won_value or 0)
        total_closed = int(row.total_closed or 0)
        deals_won = int(row.deals_won or 0)
        deals_lost = int(row.deals_lost or 0)
        win_rate = (deals_won / total_closed * 100) if total_closed > 0 else None
        closed_map[agent_id] = {
            "deals_won": deals_won,
            "deals_lost": deals_lost,
            "total_deals": total_closed,
            "won_value": float(won_value),
            "win_rate": round(win_rate, 1) if win_rate is not None else None,
        }

    activity_query = (
        db.query(
            Lead.owner_agent_id.label("agent_id"),
            func.count(func.distinct(Message.id)).label("activity_count"),
        )
        .join(Conversation, Conversation.person_id == Lead.person_id)
        .join(Message, Message.conversation_id == Conversation.id)
        .join(CrmAgent, CrmAgent.id == Lead.owner_agent_id)
        .filter(Lead.is_active.is_(True))
        .filter(Lead.owner_agent_id.isnot(None))
        .filter(Message.author_id == CrmAgent.person_id)
    )

    if start_at:
        activity_query = activity_query.filter(Message.created_at >= start_at)
    if end_at:
        activity_query = activity_query.filter(Message.created_at <= end_at)
    if pipeline_id:
        activity_query = activity_query.filter(Lead.pipeline_id == coerce_uuid(pipeline_id))

    activity_rows = activity_query.group_by(Lead.owner_agent_id).all()
    activity_map = {str(row.agent_id): int(row.activity_count or 0) for row in activity_rows}

    agent_ids = sorted(set(closed_map.keys()) | set(activity_map.keys()))
    if not agent_ids:
        return []

    agents = (
        db.query(CrmAgent, Person)
        .join(Person, Person.id == CrmAgent.person_id)
        .filter(CrmAgent.id.in_([coerce_uuid(a) for a in agent_ids]))
        .all()
    )
    agent_name_map: dict[str, str] = {}
    for agent, person in agents:
        name = "Unknown Agent"
        if person:
            name = person.display_name or f"{person.first_name or ''} {person.last_name or ''}".strip() or "Agent"
        agent_name_map[str(agent.id)] = name

    results: list[dict[str, Any]] = []
    for agent_id in agent_ids:
        base = closed_map.get(
            agent_id,
            {"deals_won": 0, "deals_lost": 0, "total_deals": 0, "won_value": 0.0, "win_rate": None},
        )
        results.append(
            {
                "agent_id": agent_id,
                "name": agent_name_map.get(agent_id, "Unknown Agent"),
                "deals_won": base["deals_won"],
                "deals_lost": base["deals_lost"],
                "total_deals": base["total_deals"],
                "won_value": base["won_value"],
                "win_rate": base["win_rate"],
                "activity_count": activity_map.get(agent_id, 0),
            }
        )

    results.sort(key=lambda x: float(x.get("won_value") or 0), reverse=True)
    return results


def agent_weekly_performance(
    db: Session,
    start_at: datetime,
    end_at: datetime,
) -> list[dict[str, Any]]:
    """Compute weekly performance metrics per agent.

    Returns a list of dicts with: agent_id, agent_name, resolved_count,
    median_response_seconds, median_resolution_seconds, open_backlog,
    csat_avg, sla_breach_count.
    """
    import statistics

    from app.models.comms import SurveyInvitation, SurveyResponse

    agents = db.query(CrmAgent).filter(CrmAgent.is_active.is_(True)).limit(200).all()
    if not agents:
        return []

    person_ids = [a.person_id for a in agents if a.person_id]
    persons = db.query(Person).filter(Person.id.in_(person_ids)).all() if person_ids else []
    person_map = {p.id: p for p in persons}

    from app.services.crm.inbox.sla import get_sla_targets

    sla_targets = get_sla_targets(db)

    agent_ids = [a.id for a in agents]

    # Batch-load all assignments for these agents
    all_assignments = (
        db.query(ConversationAssignment.agent_id, ConversationAssignment.conversation_id)
        .filter(ConversationAssignment.agent_id.in_(agent_ids))
        .all()
    )
    # Build agent_id -> set of conversation_ids
    agent_conv_ids: dict = {}
    all_conv_ids: set = set()
    for agent_id, conv_id in all_assignments:
        agent_conv_ids.setdefault(agent_id, set()).add(conv_id)
        all_conv_ids.add(conv_id)

    if not all_conv_ids:
        return [
            {
                "agent_id": str(a.id),
                "agent_name": (person.display_name if person else "Unknown"),
                "resolved_count": 0,
                "median_response_seconds": None,
                "median_resolution_seconds": None,
                "open_backlog": 0,
                "csat_avg": None,
                "sla_breach_count": 0,
            }
            for a in agents
            for person in [person_map.get(a.person_id)]
        ]

    # Batch-load resolved conversations in the period
    resolved_convs = (
        db.query(Conversation)
        .filter(
            Conversation.id.in_(all_conv_ids),
            Conversation.status == ConversationStatus.resolved,
            Conversation.resolved_at >= start_at,
            Conversation.resolved_at <= end_at,
        )
        .all()
    )
    resolved_by_id = {c.id: c for c in resolved_convs}

    # Batch-load open/pending conversations for backlog
    open_convs = (
        db.query(Conversation.id)
        .filter(
            Conversation.id.in_(all_conv_ids),
            Conversation.status.in_([ConversationStatus.open, ConversationStatus.pending]),
            Conversation.is_active.is_(True),
        )
        .all()
    )
    open_conv_ids = {row[0] for row in open_convs}

    # Batch-load CSAT ratings for resolved conversation persons
    all_resolved_person_ids = list({c.person_id for c in resolved_convs})
    ratings_by_person: dict = {}
    if all_resolved_person_ids:
        rating_rows = (
            db.query(SurveyInvitation.person_id, SurveyResponse.rating)
            .join(SurveyInvitation, SurveyInvitation.id == SurveyResponse.invitation_id)
            .filter(
                SurveyInvitation.person_id.in_(all_resolved_person_ids),
                SurveyResponse.rating.isnot(None),
                SurveyResponse.completed_at >= start_at,
                SurveyResponse.completed_at <= end_at,
            )
            .all()
        )
        for person_id, rating in rating_rows:
            if rating is not None:
                ratings_by_person.setdefault(person_id, []).append(rating)

    # Compute per-agent metrics from batch-loaded data
    results: list[dict[str, Any]] = []
    for agent in agents:
        person = person_map.get(agent.person_id)
        agent_name = person.display_name if person else "Unknown"
        conv_ids = agent_conv_ids.get(agent.id, set())

        agent_resolved = [resolved_by_id[cid] for cid in conv_ids if cid in resolved_by_id]
        response_times = [c.response_time_seconds for c in agent_resolved if c.response_time_seconds is not None]
        resolution_times = [c.resolution_time_seconds for c in agent_resolved if c.resolution_time_seconds is not None]
        open_backlog = sum(1 for cid in conv_ids if cid in open_conv_ids)

        # CSAT from resolved conversation persons
        agent_person_ids = {c.person_id for c in agent_resolved}
        agent_ratings = []
        for pid in agent_person_ids:
            agent_ratings.extend(ratings_by_person.get(pid, []))
        csat_avg = round(sum(agent_ratings) / len(agent_ratings), 2) if agent_ratings else None

        # SLA breach count
        breach_count = 0
        for conv in agent_resolved:
            priority = conv.priority.value if conv.priority else "none"
            resp_target = sla_targets["response"].get(priority, 1440)
            res_target = sla_targets["resolution"].get(priority, 4320)
            if conv.response_time_seconds and conv.response_time_seconds > resp_target * 60:
                breach_count += 1
            if conv.resolution_time_seconds and conv.resolution_time_seconds > res_target * 60:
                breach_count += 1

        results.append(
            {
                "agent_id": str(agent.id),
                "agent_name": agent_name,
                "resolved_count": len(agent_resolved),
                "median_response_seconds": int(statistics.median(response_times)) if response_times else None,
                "median_resolution_seconds": int(statistics.median(resolution_times)) if resolution_times else None,
                "open_backlog": open_backlog,
                "csat_avg": csat_avg,
                "sla_breach_count": breach_count,
            }
        )

    return results


def _percentile(values: list[int], pct: float) -> int | None:
    """Nearest-rank percentile (pct in 0..1). Returns None for empty input."""
    if not values:
        return None
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(pct * (len(ordered) - 1))))
    return int(ordered[k])


def queue_wait_metrics(
    db: Session,
    start_at: datetime,
    end_at: datetime,
    *,
    team_id: str | None = None,
) -> dict[str, Any]:
    """Queue-wait statistics over completed queue cycles.

    Newer rows use last_queue_* fields so re-queued conversations are measured
    against their current queue cycle. Legacy rows fall back to
    first_assigned_at - queued_at.
    """
    import statistics

    query = db.query(
        Conversation.id,
        Conversation.queued_at,
        Conversation.first_assigned_at,
        Conversation.last_queue_assigned_at,
        Conversation.last_queue_wait_seconds,
    ).filter(
        (
            (Conversation.last_queue_assigned_at.isnot(None))
            & (Conversation.last_queue_assigned_at >= start_at)
            & (Conversation.last_queue_assigned_at <= end_at)
        )
        | (
            (Conversation.last_queue_assigned_at.is_(None))
            & (Conversation.queued_at.isnot(None))
            & (Conversation.first_assigned_at.isnot(None))
            & (Conversation.first_assigned_at >= start_at)
            & (Conversation.first_assigned_at <= end_at)
        )
    )
    if team_id:
        query = query.join(ConversationAssignment, ConversationAssignment.conversation_id == Conversation.id).filter(
            ConversationAssignment.team_id == coerce_uuid(team_id)
        )

    rows = query.all()

    waits: list[int] = []
    by_day: dict[str, list[int]] = {}
    for _cid, queued_at, first_assigned_at, last_queue_assigned_at, last_queue_wait_seconds in rows:
        if last_queue_assigned_at is not None and last_queue_wait_seconds is not None:
            assigned_at = last_queue_assigned_at
            wait = int(last_queue_wait_seconds)
        else:
            if queued_at is None or first_assigned_at is None:
                continue
            if queued_at.tzinfo is None:
                queued_at = queued_at.replace(tzinfo=UTC)
            assigned_at = first_assigned_at
            if assigned_at.tzinfo is None:
                assigned_at = assigned_at.replace(tzinfo=UTC)
            wait = int((assigned_at - queued_at).total_seconds())
        if wait < 0:
            continue
        waits.append(wait)
        day = assigned_at.date().isoformat()
        by_day.setdefault(day, []).append(wait)

    def _summary(values: list[int]) -> dict[str, Any]:
        return {
            "count": len(values),
            "avg_seconds": int(statistics.fmean(values)) if values else None,
            "median_seconds": int(statistics.median(values)) if values else None,
            "p90_seconds": _percentile(values, 0.9),
        }

    return {
        "overall": _summary(waits),
        "by_day": [{"day": day, **_summary(vals)} for day, vals in sorted(by_day.items())],
    }


def issue_classification_breakdown(
    db: Session,
    start_at: datetime,
    end_at: datetime,
) -> dict[str, Any]:
    """Conversation volume + median resolution time grouped by AI-classified
    department, plus a tag-frequency breakdown, over conversations created in the
    window."""
    import statistics

    from app.models.crm.conversation import ConversationTag

    rows = (
        db.query(Conversation.id, Conversation.metadata_, Conversation.resolution_time_seconds)
        .filter(Conversation.created_at >= start_at)
        .filter(Conversation.created_at <= end_at)
        .all()
    )

    dept_counts: dict[str, int] = {}
    dept_resolution: dict[str, list[int]] = {}
    for _cid, metadata, resolution_seconds in rows:
        meta = metadata if isinstance(metadata, dict) else {}
        ai_raw = meta.get("ai_intake")
        ai = ai_raw if isinstance(ai_raw, dict) else {}
        department = ai.get("department") or ai.get("routing_department") or "unclassified"
        dept_counts[department] = dept_counts.get(department, 0) + 1
        if resolution_seconds is not None:
            dept_resolution.setdefault(department, []).append(int(resolution_seconds))

    departments = [
        {
            "department": dept,
            "count": count,
            "median_resolution_seconds": (
                int(statistics.median(dept_resolution[dept])) if dept_resolution.get(dept) else None
            ),
        }
        for dept, count in sorted(dept_counts.items(), key=lambda kv: kv[1], reverse=True)
    ]

    tag_rows = (
        db.query(ConversationTag.tag, func.count(ConversationTag.id))
        .join(Conversation, Conversation.id == ConversationTag.conversation_id)
        .filter(Conversation.created_at >= start_at)
        .filter(Conversation.created_at <= end_at)
        .group_by(ConversationTag.tag)
        .order_by(func.count(ConversationTag.id).desc())
        .all()
    )
    tags = [{"tag": tag, "count": int(count)} for tag, count in tag_rows]

    return {"departments": departments, "tags": tags}
