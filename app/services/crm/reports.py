from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.crm.conversation import Conversation, ConversationAssignment, Message
from app.models.crm.enums import ChannelType, ConversationStatus, MessageDirection
from app.models.crm.sales import Lead, PipelineStage
from app.models.crm.team import CrmAgent, CrmAgentTeam
from app.models.tickets import Ticket, TicketSlaEvent, TicketStatus
from app.models.workforce import WorkOrder
from app.models.projects import Project, ProjectTask
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
        query = query.filter(
            Ticket.assigned_to_person_id.in_([coerce_uuid(pid) for pid in person_ids])
        )

    tickets = query.all()
    totals = {"total": len(tickets), "open": 0, "closed": 0}
    resolution_hours = []
    for ticket in tickets:
        totals[_status_group(ticket.status)] += 1
        if ticket.resolved_at:
            delta = ticket.resolved_at - ticket.created_at
            resolution_hours.append(delta.total_seconds() / 3600)

    avg_resolution = (
        sum(resolution_hours) / len(resolution_hours) if resolution_hours else None
    )

    sla_query = db.query(TicketSlaEvent).join(Ticket)
    if start_at:
        sla_query = sla_query.filter(TicketSlaEvent.created_at >= start_at)
    if end_at:
        sla_query = sla_query.filter(TicketSlaEvent.created_at <= end_at)
    if person_ids is not None:
        if not person_ids:
            sla_query = sla_query.filter(TicketSlaEvent.id == None)  # noqa: E711
        else:
            sla_query = sla_query.filter(
                Ticket.assigned_to_person_id.in_([coerce_uuid(pid) for pid in person_ids])
            )
    sla_events = sla_query.all()
    sla_totals = {"total": 0, "met": 0, "breached": 0, "by_event_type": {}, "by_priority": {}}
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
            priority_bucket = sla_totals["by_priority"].setdefault(
                priority_key, {"total": 0, "met": 0, "breached": 0}
            )
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
            assignment_query = assignment_query.filter(
                ConversationAssignment.agent_id == coerce_uuid(agent_id)
            )
        if team_id:
            assignment_query = assignment_query.filter(
                ConversationAssignment.team_id == coerce_uuid(team_id)
            )
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
        if inbound_time and outbound_time:
            response_times.append((outbound_time - inbound_time).total_seconds() / 60)
        if convo.status == ConversationStatus.resolved and inbound_time and convo.updated_at:
            resolution_times.append((convo.updated_at - inbound_time).total_seconds() / 60)

    avg_response_minutes = (
        sum(response_times) / len(response_times) if response_times else None
    )
    avg_resolution_minutes = (
        sum(resolution_times) / len(resolution_times) if resolution_times else None
    )

    channel_volume = (
        db.query(Message.channel_type, func.count(Message.id))
        .group_by(Message.channel_type)
        .all()
    )
    channel_volume_map = {str(channel): count for channel, count in channel_volume}

    return {
        "messages": {
            "total": total_messages,
            "inbound": inbound_messages,
            "outbound": outbound_messages,
            "by_channel": channel_volume_map,
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
        query = query.filter(
            WorkOrder.assigned_to_person_id.in_([coerce_uuid(pid) for pid in person_ids])
        )
    work_orders = query.all()
    status_counts = {}
    completion_hours = []
    for order in work_orders:
        key = order.status.value if order.status else "unknown"
        status_counts[key] = status_counts.get(key, 0) + 1
        if order.completed_at and order.started_at:
            completion_hours.append(
                (order.completed_at - order.started_at).total_seconds() / 3600
            )
    avg_completion_hours = (
        sum(completion_hours) / len(completion_hours) if completion_hours else None
    )
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
    project_status = {}
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
    task_status = {}
    for task in tasks:
        key = task.status.value if task.status else "unknown"
        task_status[key] = task_status.get(key, 0) + 1

    return {
        "projects": {"total": len(projects), "status": project_status},
        "tasks": {"total": len(tasks), "status": task_status},
    }
