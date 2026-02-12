from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection
from app.models.crm.sales import Lead, PipelineStage
from app.models.crm.team import CrmAgent, CrmAgentTeam
from app.models.person import Person
from app.models.projects import Project, ProjectTask
from app.models.tickets import Ticket, TicketSlaEvent, TicketStatus
from app.models.workforce import WorkOrder
from app.services.common import coerce_uuid


def _status_group(status: TicketStatus) -> str:
    if status in {TicketStatus.resolved, TicketStatus.closed, TicketStatus.canceled}:
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
    message_time = func.coalesce(
        Message.received_at,
        Message.sent_at,
        Message.created_at,
    )
    if start_at:
        message_query = message_query.filter(message_time >= start_at)
    if end_at:
        message_query = message_query.filter(message_time <= end_at)
    if channel_type:
        try:
            channel_value = ChannelType(channel_type)
        except ValueError:
            channel_value = None
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

    conversation_query = db.query(Conversation)
    if start_at:
        conversation_query = conversation_query.filter(Conversation.created_at >= start_at)
    if end_at:
        conversation_query = conversation_query.filter(Conversation.created_at <= end_at)
    if conversation_ids is not None:
        conversation_query = conversation_query.filter(Conversation.id.in_(conversation_ids))
    conversations = conversation_query.all()

    response_times = []
    resolution_times = []

    if conversations:
        convo_ids = [c.id for c in conversations]

        # Batch query: first inbound message per conversation using window function

        inbound_subq = (
            db.query(
                Message.conversation_id,
                func.coalesce(Message.received_at, Message.created_at).label("msg_time"),
                func.row_number()
                .over(
                    partition_by=Message.conversation_id,
                    order_by=func.coalesce(Message.received_at, Message.created_at).asc(),
                )
                .label("rn"),
            )
            .filter(Message.conversation_id.in_(convo_ids))
            .filter(Message.direction == MessageDirection.inbound)
            .subquery()
        )
        first_inbound = (
            db.query(inbound_subq.c.conversation_id, inbound_subq.c.msg_time).filter(inbound_subq.c.rn == 1).all()
        )
        inbound_map = {row[0]: row[1] for row in first_inbound}

        # Batch query: first outbound message per conversation
        outbound_subq = (
            db.query(
                Message.conversation_id,
                func.coalesce(Message.sent_at, Message.created_at).label("msg_time"),
                func.row_number()
                .over(
                    partition_by=Message.conversation_id,
                    order_by=func.coalesce(Message.sent_at, Message.created_at).asc(),
                )
                .label("rn"),
            )
            .filter(Message.conversation_id.in_(convo_ids))
            .filter(Message.direction == MessageDirection.outbound)
            .subquery()
        )
        first_outbound = (
            db.query(outbound_subq.c.conversation_id, outbound_subq.c.msg_time).filter(outbound_subq.c.rn == 1).all()
        )
        outbound_map = {row[0]: row[1] for row in first_outbound}

        # Calculate response and resolution times from batch results
        for convo in conversations:
            inbound_time = inbound_map.get(convo.id)
            outbound_time = outbound_map.get(convo.id)
            if inbound_time and outbound_time:
                response_times.append((outbound_time - inbound_time).total_seconds() / 60)
            if convo.status == ConversationStatus.resolved and inbound_time and convo.updated_at:
                resolution_times.append((convo.updated_at - inbound_time).total_seconds() / 60)

    avg_response_minutes = sum(response_times) / len(response_times) if response_times else None
    avg_resolution_minutes = sum(resolution_times) / len(resolution_times) if resolution_times else None

    channel_volume = db.query(Message.channel_type, func.count(Message.id)).group_by(Message.channel_type).all()
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
    """Get per-agent performance metrics for the CRM dashboard.

    Returns a list of agent stats with resolved conversations, FRT, resolution time.
    """
    from app.models.person import Person

    # Get list of agents to analyze
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

    # Get person names in bulk
    person_ids = [agent.person_id for agent in agents if agent.person_id]
    persons = db.query(Person).filter(Person.id.in_(person_ids)).all() if person_ids else []
    person_map = {person.id: person for person in persons}

    agent_stats: list[dict[str, Any]] = []
    for agent in agents:
        # Get conversations assigned to this agent
        assignment_query = db.query(ConversationAssignment.conversation_id).filter(
            ConversationAssignment.agent_id == agent.id
        )
        conversation_ids = [row[0] for row in assignment_query.all()]

        if not conversation_ids:
            person = person_map.get(agent.person_id)
            name = (
                (person.display_name or f"{person.first_name or ''} {person.last_name or ''}".strip() or "Agent")
                if person
                else "Agent"
            )
            agent_stats.append(
                {
                    "agent_id": str(agent.id),
                    "name": name,
                    "total_conversations": 0,
                    "resolved_conversations": 0,
                    "avg_first_response_minutes": None,
                    "avg_resolution_minutes": None,
                }
            )
            continue

        # Query conversations
        convo_query = db.query(Conversation).filter(Conversation.id.in_(conversation_ids))
        if start_at:
            convo_query = convo_query.filter(Conversation.created_at >= start_at)
        if end_at:
            convo_query = convo_query.filter(Conversation.created_at <= end_at)
        if channel_type:
            try:
                channel_value = ChannelType(channel_type)
            except ValueError:
                channel_value = None
            if channel_value:
                convo_query = convo_query.filter(
                    db.query(Message.id)
                    .filter(Message.conversation_id == Conversation.id)
                    .filter(Message.channel_type == channel_value)
                    .exists()
                )

        conversations = convo_query.all()
        total = len(conversations)
        resolved = sum(1 for c in conversations if c.status == ConversationStatus.resolved)

        # Calculate FRT and resolution time
        response_times = []
        resolution_times = []
        for convo in conversations:
            inbound = (
                db.query(Message)
                .filter(Message.conversation_id == convo.id)
                .filter(Message.direction == MessageDirection.inbound)
                .order_by(func.coalesce(Message.received_at, Message.created_at).asc())
                .first()
            )
            outbound = (
                db.query(Message)
                .filter(Message.conversation_id == convo.id)
                .filter(Message.direction == MessageDirection.outbound)
                .order_by(func.coalesce(Message.sent_at, Message.created_at).asc())
                .first()
            )
            inbound_time = inbound.received_at or inbound.created_at if inbound else None
            outbound_time = outbound.sent_at or outbound.created_at if outbound else None
            if inbound_time and outbound_time and outbound_time > inbound_time:
                response_times.append((outbound_time - inbound_time).total_seconds() / 60)
            if convo.status == ConversationStatus.resolved and inbound_time and convo.updated_at:
                resolution_times.append((convo.updated_at - inbound_time).total_seconds() / 60)

        avg_frt = sum(response_times) / len(response_times) if response_times else None
        avg_resolution = sum(resolution_times) / len(resolution_times) if resolution_times else None

        person = person_map.get(agent.person_id)
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
                "resolved_conversations": resolved,
                "avg_first_response_minutes": round(avg_frt, 1) if avg_frt is not None else None,
                "avg_resolution_minutes": round(avg_resolution, 1) if avg_resolution is not None else None,
            }
        )

    # Sort by resolved conversations descending
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

    trend_data = []
    current_date = start_at.date()
    end_date = end_at.date()

    while current_date <= end_date:
        day_start = datetime.combine(current_date, datetime.min.time()).replace(tzinfo=start_at.tzinfo)
        day_end = day_start + timedelta(days=1)

        query = db.query(Conversation).filter(
            Conversation.created_at >= day_start,
            Conversation.created_at < day_end,
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
            query = query.filter(Conversation.id.in_(conversation_ids))
        if channel_type:
            try:
                channel_value = ChannelType(channel_type)
            except ValueError:
                channel_value = None
            if channel_value:
                query = query.filter(
                    db.query(Message.id)
                    .filter(Message.conversation_id == Conversation.id)
                    .filter(Message.channel_type == channel_value)
                    .exists()
                )

        conversations = query.all()
        total = len(conversations)
        resolved = sum(1 for c in conversations if c.status == ConversationStatus.resolved)

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

    query = db.query(Lead).filter(Lead.is_active.is_(True))

    if pipeline_id:
        query = query.filter(Lead.pipeline_id == coerce_uuid(pipeline_id))
    if start_at:
        query = query.filter(Lead.created_at >= start_at)
    if end_at:
        query = query.filter(Lead.created_at <= end_at)
    if owner_agent_id:
        query = query.filter(Lead.owner_agent_id == coerce_uuid(owner_agent_id))

    leads = query.all()

    total_value = Decimal("0.00")
    weighted_value = Decimal("0.00")
    open_deals = 0
    won_deals = 0
    lost_deals = 0
    won_value = Decimal("0.00")

    for lead in leads:
        if lead.estimated_value:
            total_value += lead.estimated_value
            if lead.probability is not None:
                weighted_value += lead.estimated_value * Decimal(lead.probability) / Decimal(100)

        if lead.status == LeadStatus.won:
            won_deals += 1
            if lead.estimated_value:
                won_value += lead.estimated_value
        elif lead.status == LeadStatus.lost:
            lost_deals += 1
        else:
            open_deals += 1

    total_closed = won_deals + lost_deals
    win_rate = (won_deals / total_closed * 100) if total_closed > 0 else None
    avg_deal_size = (won_value / Decimal(won_deals)) if won_deals > 0 else None

    # Get stage breakdown
    stages_query = db.query(PipelineStage).filter(PipelineStage.is_active.is_(True))
    if pipeline_id:
        stages_query = stages_query.filter(PipelineStage.pipeline_id == coerce_uuid(pipeline_id))
    stages = stages_query.order_by(PipelineStage.order_index.asc()).all()

    stage_breakdown = []
    for stage in stages:
        stage_leads = [lead for lead in leads if lead.stage_id == stage.id]
        stage_value = sum((lead.estimated_value or Decimal(0)) for lead in stage_leads)
        stage_breakdown.append(
            {
                "id": str(stage.id),
                "name": stage.name,
                "count": len(stage_leads),
                "value": float(stage_value),
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

    leads = query.all()

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
        weighted_value = sum(
            (lead.estimated_value or Decimal(0)) * Decimal(lead.probability or 50) / Decimal(100)
            for lead in month_leads
        )

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

    # Get all agents who own leads
    leads_query = db.query(Lead).filter(
        Lead.is_active.is_(True),
        Lead.owner_agent_id.isnot(None),
    )

    if start_at:
        leads_query = leads_query.filter(Lead.created_at >= start_at)
    if end_at:
        leads_query = leads_query.filter(Lead.created_at <= end_at)
    if pipeline_id:
        leads_query = leads_query.filter(Lead.pipeline_id == coerce_uuid(pipeline_id))

    leads = leads_query.all()

    # Group leads by owner agent
    agent_leads: dict[str, list] = {}
    for lead in leads:
        agent_id = str(lead.owner_agent_id)
        if agent_id not in agent_leads:
            agent_leads[agent_id] = []
        agent_leads[agent_id].append(lead)

    # Get agent names
    agent_ids = list(agent_leads.keys())
    agents = db.query(CrmAgent).filter(CrmAgent.id.in_([coerce_uuid(a) for a in agent_ids])).all()
    agent_map = {str(a.id): a for a in agents}

    # Get person names
    person_ids = [a.person_id for a in agents if a.person_id]
    persons = db.query(Person).filter(Person.id.in_(person_ids)).all() if person_ids else []
    person_map = {p.id: p for p in persons}

    results: list[dict[str, Any]] = []
    for agent_id, agent_lead_list in agent_leads.items():
        agent = agent_map.get(agent_id)
        person = person_map.get(agent.person_id) if agent and agent.person_id else None

        name = "Unknown Agent"
        if person:
            name = person.display_name or f"{person.first_name or ''} {person.last_name or ''}".strip() or "Agent"

        won = [lead for lead in agent_lead_list if lead.status == LeadStatus.won]
        lost = [lead for lead in agent_lead_list if lead.status == LeadStatus.lost]

        won_value = sum((lead.estimated_value or Decimal(0)) for lead in won)
        total_closed = len(won) + len(lost)
        win_rate = (len(won) / total_closed * 100) if total_closed > 0 else None

        results.append(
            {
                "agent_id": agent_id,
                "name": name,
                "deals_won": len(won),
                "deals_lost": len(lost),
                "total_deals": len(agent_lead_list),
                "won_value": float(won_value),
                "win_rate": round(win_rate, 1) if win_rate is not None else None,
            }
        )

    # Sort by won value descending
    results.sort(key=lambda x: float(x.get("won_value") or 0), reverse=True)
    return results
