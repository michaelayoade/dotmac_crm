from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import String, cast
from sqlalchemy.orm import Session

from app.models.ai_insight import AIInsight
from app.models.crm.campaign import Campaign
from app.models.crm.enums import CampaignStatus
from app.models.dispatch import WorkOrderAssignmentQueue
from app.models.performance import AgentPerformanceSnapshot
from app.models.projects import Project, ProjectStatus
from app.models.subscriber import Subscriber
from app.models.tickets import Ticket, TicketStatus
from app.models.vendor import ProjectQuote, ProjectQuoteStatus
from app.models.workforce import WorkOrder, WorkOrderStatus


def scan_tickets_for_persona(
    db: Session, persona_key: str, *, lookback_hours: int = 24, limit: int = 50
) -> list[tuple[str, str, dict[str, Any]]]:
    """
    Return a list of (entity_type, entity_id, params) that should be analyzed.
    Basic heuristic: open-ish tickets updated recently that do not already have a recent insight for this persona.
    """
    since = datetime.now(UTC) - timedelta(hours=max(1, lookback_hours))

    recent_insight_ticket_ids = (
        db.query(AIInsight.entity_id)
        .filter(AIInsight.persona_key == persona_key)
        .filter(AIInsight.entity_type == "ticket")
        .filter(AIInsight.created_at >= since)
        .filter(AIInsight.entity_id.isnot(None))
        .subquery()
    )

    rows = (
        db.query(Ticket.id)
        .filter(Ticket.is_active.is_(True))
        .filter(Ticket.updated_at >= since)
        .filter(
            Ticket.status.in_(
                [
                    TicketStatus.new,
                    TicketStatus.open,
                    TicketStatus.pending,
                    TicketStatus.waiting_on_customer,
                    TicketStatus.on_hold,
                ]
            )
        )
        .filter(~cast(Ticket.id, String).in_(db.query(recent_insight_ticket_ids.c.entity_id)))
        .order_by(Ticket.updated_at.desc())
        .limit(max(1, limit))
        .all()
    )

    results: list[tuple[str, str, dict[str, Any]]] = []
    for (ticket_id,) in rows:
        results.append(("ticket", str(ticket_id), {"ticket_id": str(ticket_id)}))
    return results


def scan_projects_for_persona(
    db: Session, persona_key: str, *, lookback_hours: int = 48, limit: int = 50
) -> list[tuple[str, str, dict[str, Any]]]:
    since = datetime.now(UTC) - timedelta(hours=max(1, lookback_hours))

    recent_ids = (
        db.query(AIInsight.entity_id)
        .filter(AIInsight.persona_key == persona_key)
        .filter(AIInsight.entity_type == "project")
        .filter(AIInsight.created_at >= since)
        .filter(AIInsight.entity_id.isnot(None))
        .subquery()
    )

    rows = (
        db.query(Project.id)
        .filter(Project.is_active.is_(True))
        .filter(Project.updated_at >= since)
        .filter(
            Project.status.in_([ProjectStatus.open, ProjectStatus.planned, ProjectStatus.active, ProjectStatus.on_hold])
        )
        .filter(~cast(Project.id, String).in_(db.query(recent_ids.c.entity_id)))
        .order_by(Project.updated_at.desc())
        .limit(max(1, limit))
        .all()
    )

    return [("project", str(project_id), {"project_id": str(project_id)}) for (project_id,) in rows]


def scan_campaigns_for_persona(
    db: Session, persona_key: str, *, lookback_hours: int = 48, limit: int = 50
) -> list[tuple[str, str, dict[str, Any]]]:
    since = datetime.now(UTC) - timedelta(hours=max(1, lookback_hours))

    recent_ids = (
        db.query(AIInsight.entity_id)
        .filter(AIInsight.persona_key == persona_key)
        .filter(AIInsight.entity_type == "campaign")
        .filter(AIInsight.created_at >= since)
        .filter(AIInsight.entity_id.isnot(None))
        .subquery()
    )

    rows = (
        db.query(Campaign.id)
        .filter(Campaign.is_active.is_(True))
        .filter(Campaign.updated_at >= since)
        .filter(Campaign.status.in_([CampaignStatus.scheduled, CampaignStatus.sending, CampaignStatus.sent]))
        .filter(~cast(Campaign.id, String).in_(db.query(recent_ids.c.entity_id)))
        .order_by(Campaign.updated_at.desc())
        .limit(max(1, limit))
        .all()
    )

    return [("campaign", str(campaign_id), {"campaign_id": str(campaign_id)}) for (campaign_id,) in rows]


def scan_dispatch_for_persona(
    db: Session, persona_key: str, *, lookback_hours: int = 24, limit: int = 50
) -> list[tuple[str, str, dict[str, Any]]]:
    since = datetime.now(UTC) - timedelta(hours=max(1, lookback_hours))

    recent_ids = (
        db.query(AIInsight.entity_id)
        .filter(AIInsight.persona_key == persona_key)
        .filter(AIInsight.entity_type == "work_order")
        .filter(AIInsight.created_at >= since)
        .filter(AIInsight.entity_id.isnot(None))
        .subquery()
    )

    # Prefer work orders that are queued for assignment, then active statuses.
    queued_ids = (
        db.query(WorkOrderAssignmentQueue.work_order_id).filter(WorkOrderAssignmentQueue.created_at >= since).subquery()
    )

    rows = (
        db.query(WorkOrder.id)
        .filter(WorkOrder.is_active.is_(True))
        .filter(WorkOrder.updated_at >= since)
        .filter(
            (WorkOrder.id.in_(db.query(queued_ids.c.work_order_id)))
            | (
                WorkOrder.status.in_(
                    [WorkOrderStatus.scheduled, WorkOrderStatus.dispatched, WorkOrderStatus.in_progress]
                )
            )
        )
        .filter(~cast(WorkOrder.id, String).in_(db.query(recent_ids.c.entity_id)))
        .order_by(WorkOrder.updated_at.desc())
        .limit(max(1, limit))
        .all()
    )

    return [("work_order", str(work_order_id), {"work_order_id": str(work_order_id)}) for (work_order_id,) in rows]


def scan_vendor_quotes_for_persona(
    db: Session, persona_key: str, *, lookback_hours: int = 72, limit: int = 50
) -> list[tuple[str, str, dict[str, Any]]]:
    since = datetime.now(UTC) - timedelta(hours=max(1, lookback_hours))

    recent_ids = (
        db.query(AIInsight.entity_id)
        .filter(AIInsight.persona_key == persona_key)
        .filter(AIInsight.entity_type == "vendor_quote")
        .filter(AIInsight.created_at >= since)
        .filter(AIInsight.entity_id.isnot(None))
        .subquery()
    )

    rows = (
        db.query(ProjectQuote.id)
        .filter(ProjectQuote.is_active.is_(True))
        .filter(ProjectQuote.updated_at >= since)
        .filter(
            ProjectQuote.status.in_(
                [
                    ProjectQuoteStatus.submitted,
                    ProjectQuoteStatus.under_review,
                    ProjectQuoteStatus.revision_requested,
                ]
            )
        )
        .filter(~cast(ProjectQuote.id, String).in_(db.query(recent_ids.c.entity_id)))
        .order_by(ProjectQuote.updated_at.desc())
        .limit(max(1, limit))
        .all()
    )

    return [("vendor_quote", str(quote_id), {"quote_id": str(quote_id)}) for (quote_id,) in rows]


def scan_performance_snapshots_for_persona(
    db: Session, persona_key: str, *, lookback_hours: int = 168, limit: int = 50
) -> list[tuple[str, str, dict[str, Any]]]:
    since = datetime.now(UTC) - timedelta(hours=max(1, lookback_hours))

    recent_ids = (
        db.query(AIInsight.entity_id)
        .filter(AIInsight.persona_key == persona_key)
        .filter(AIInsight.entity_type == "performance_snapshot")
        .filter(AIInsight.created_at >= since)
        .filter(AIInsight.entity_id.isnot(None))
        .subquery()
    )

    rows = (
        db.query(AgentPerformanceSnapshot)
        .filter(AgentPerformanceSnapshot.created_at >= since)
        .order_by(AgentPerformanceSnapshot.created_at.desc())
        .limit(max(1, limit))
        .all()
    )

    recent_id_set = {row[0] for row in db.query(recent_ids.c.entity_id).all()}

    results: list[tuple[str, str, dict[str, Any]]] = []
    for snap in rows:
        if str(snap.id) in recent_id_set:
            continue
        results.append(
            (
                "performance_snapshot",
                str(snap.id),
                {
                    "person_id": str(snap.person_id),
                    "period_start": snap.score_period_start.isoformat(),
                    "period_end": snap.score_period_end.isoformat(),
                },
            )
        )
    return results


def scan_subscribers_for_persona(
    db: Session, persona_key: str, *, lookback_hours: int = 72, limit: int = 50
) -> list[tuple[str, str, dict[str, Any]]]:
    since = datetime.now(UTC) - timedelta(hours=max(1, lookback_hours))

    recent_ids = (
        db.query(AIInsight.entity_id)
        .filter(AIInsight.persona_key == persona_key)
        .filter(AIInsight.entity_type == "subscriber")
        .filter(AIInsight.created_at >= since)
        .filter(AIInsight.entity_id.isnot(None))
        .subquery()
    )

    rows = (
        db.query(Subscriber.id)
        .filter(Subscriber.is_active.is_(True))
        .filter(Subscriber.updated_at >= since)
        .filter(~cast(Subscriber.id, String).in_(db.query(recent_ids.c.entity_id)))
        .order_by(Subscriber.updated_at.desc())
        .limit(max(1, limit))
        .all()
    )
    return [("subscriber", str(subscriber_id), {"subscriber_id": str(subscriber_id)}) for (subscriber_id,) in rows]


batch_scanners: dict[str, Any] = {
    "tickets": scan_tickets_for_persona,
    "projects": scan_projects_for_persona,
    "campaigns": scan_campaigns_for_persona,
    "dispatch": scan_dispatch_for_persona,
    "vendors": scan_vendor_quotes_for_persona,
    "performance": scan_performance_snapshots_for_persona,
    "customer_success": scan_subscribers_for_persona,
}
