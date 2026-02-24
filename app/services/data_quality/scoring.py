"""Data quality scoring for all primary domain entities.

Each scorer evaluates field completeness for a single entity and returns an
``EntityQualityResult``.  Weights reflect how much each field contributes to
useful analytics, reporting, and AI analysis.

These scorers are designed to be:
- Cheap (no heavy joins — simple existence checks + count queries)
- Reusable (called by the data quality dashboard, the AI readiness gate, and batch reports)
- Consistent (identical interface across all domains)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.services.common import coerce_uuid

# ---------------------------------------------------------------------------
# Base types
# ---------------------------------------------------------------------------


@dataclass
class EntityQualityResult:
    """Quality assessment for a single entity."""

    entity_type: str
    entity_id: str
    score: float  # 0.0-1.0 weighted composite
    field_scores: dict[str, float] = field(default_factory=dict)  # per-signal 0.0-1.0
    missing_fields: list[str] = field(default_factory=list)  # fields scoring 0

    @property
    def sufficient(self) -> bool:
        return self.score >= 0.3

    def pct(self) -> int:
        """Score as a 0-100 integer for display."""
        return round(self.score * 100)


def _weighted_score(scores: dict[str, float], weights: dict[str, float]) -> float:
    total = sum(scores.get(k, 0.0) * w for k, w in weights.items())
    return round(max(0.0, min(1.0, total)), 3)


def _missing(scores: dict[str, float]) -> list[str]:
    return [k for k, v in scores.items() if v == 0.0]


# ---------------------------------------------------------------------------
# Ticket
# ---------------------------------------------------------------------------


def score_ticket_quality(db: Session, ticket_id: str) -> EntityQualityResult:
    from app.models.tickets import Ticket, TicketComment, TicketSlaEvent

    ticket = db.get(Ticket, coerce_uuid(ticket_id))
    if not ticket:
        return EntityQualityResult("ticket", ticket_id, 0.0, {}, ["ticket_not_found"])

    s: dict[str, float] = {}
    s["title"] = 1.0 if ticket.title and len(ticket.title.strip()) > 5 else 0.0
    s["description"] = min(1.0, len((ticket.description or "").strip()) / 30)
    s["status"] = 1.0 if ticket.status else 0.0
    s["priority"] = 1.0 if ticket.priority else 0.0
    s["customer"] = 1.0 if ticket.customer_person_id else 0.0
    s["assignee"] = 1.0 if ticket.assigned_to_person_id else 0.0
    s["ticket_type"] = 1.0 if ticket.ticket_type else 0.0

    comment_count = db.query(func.count(TicketComment.id)).filter(TicketComment.ticket_id == ticket.id).scalar() or 0
    s["comments"] = min(1.0, comment_count / 2)

    sla_count = db.query(func.count(TicketSlaEvent.id)).filter(TicketSlaEvent.ticket_id == ticket.id).scalar() or 0
    s["sla_events"] = 1.0 if sla_count > 0 else 0.0
    s["tags"] = 1.0 if ticket.tags else 0.0

    weights = {
        "title": 0.10,
        "description": 0.20,
        "status": 0.05,
        "priority": 0.05,
        "customer": 0.12,
        "assignee": 0.10,
        "ticket_type": 0.08,
        "comments": 0.15,
        "sla_events": 0.08,
        "tags": 0.07,
    }
    return EntityQualityResult("ticket", str(ticket.id), _weighted_score(s, weights), s, _missing(s))


# ---------------------------------------------------------------------------
# Conversation (CRM Inbox)
# ---------------------------------------------------------------------------


def score_conversation_quality(db: Session, conversation_id: str) -> EntityQualityResult:
    from app.models.crm.conversation import Conversation, ConversationAssignment, Message

    conv = db.get(Conversation, coerce_uuid(conversation_id))
    if not conv:
        return EntityQualityResult("conversation", conversation_id, 0.0, {}, ["conversation_not_found"])

    s: dict[str, float] = {}
    s["contact"] = 1.0 if conv.person_id else 0.0
    s["status"] = 1.0 if conv.status else 0.0
    s["subject"] = 1.0 if conv.subject and len(conv.subject.strip()) > 3 else 0.0

    msg_count = db.query(func.count(Message.id)).filter(Message.conversation_id == conv.id).scalar() or 0
    s["messages"] = min(1.0, msg_count / 3)

    inbound_count = (
        db.query(func.count(Message.id))
        .filter(Message.conversation_id == conv.id, Message.direction == "inbound")
        .scalar()
        or 0
    )
    s["has_inbound"] = 1.0 if inbound_count > 0 else 0.0

    outbound_count = (
        db.query(func.count(Message.id))
        .filter(Message.conversation_id == conv.id, Message.direction == "outbound")
        .scalar()
        or 0
    )
    s["has_outbound"] = 1.0 if outbound_count > 0 else 0.0

    assignment = (
        db.query(ConversationAssignment)
        .filter(ConversationAssignment.conversation_id == conv.id, ConversationAssignment.is_active.is_(True))
        .first()
    )
    s["assigned_agent"] = 1.0 if assignment and assignment.agent_id else 0.0
    s["assigned_team"] = 1.0 if assignment and assignment.team_id else 0.0

    weights = {
        "contact": 0.15,
        "status": 0.05,
        "subject": 0.05,
        "messages": 0.30,
        "has_inbound": 0.15,
        "has_outbound": 0.10,
        "assigned_agent": 0.10,
        "assigned_team": 0.10,
    }
    return EntityQualityResult("conversation", str(conv.id), _weighted_score(s, weights), s, _missing(s))


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


def score_project_quality(db: Session, project_id: str) -> EntityQualityResult:
    from app.models.projects import Project, ProjectTask

    project = db.get(Project, coerce_uuid(project_id))
    if not project:
        return EntityQualityResult("project", project_id, 0.0, {}, ["project_not_found"])

    s: dict[str, float] = {}
    s["name"] = 1.0 if project.name and len(project.name.strip()) > 3 else 0.0
    s["description"] = min(1.0, len((project.description or "").strip()) / 30)
    s["status"] = 1.0 if project.status else 0.0
    s["priority"] = 1.0 if project.priority else 0.0
    s["project_type"] = 1.0 if project.project_type else 0.0
    s["manager"] = 1.0 if (project.project_manager_person_id or project.manager_person_id) else 0.0
    s["owner"] = 1.0 if project.owner_person_id else 0.0
    s["due_date"] = 1.0 if project.due_at else 0.0

    task_count = (
        db.query(func.count(ProjectTask.id))
        .filter(ProjectTask.project_id == project.id, ProjectTask.is_active.is_(True))
        .scalar()
        or 0
    )
    s["tasks"] = min(1.0, task_count / 3)

    assigned_task_count = (
        db.query(func.count(ProjectTask.id))
        .filter(
            ProjectTask.project_id == project.id,
            ProjectTask.is_active.is_(True),
            ProjectTask.assigned_to_person_id.isnot(None),
        )
        .scalar()
        or 0
    )
    s["tasks_assigned"] = min(1.0, assigned_task_count / max(task_count, 1))

    weights = {
        "name": 0.05,
        "description": 0.12,
        "status": 0.05,
        "priority": 0.05,
        "project_type": 0.08,
        "manager": 0.10,
        "owner": 0.05,
        "due_date": 0.10,
        "tasks": 0.25,
        "tasks_assigned": 0.15,
    }
    return EntityQualityResult("project", str(project.id), _weighted_score(s, weights), s, _missing(s))


# ---------------------------------------------------------------------------
# Work Order (Dispatch / Field Service)
# ---------------------------------------------------------------------------


def score_work_order_quality(db: Session, work_order_id: str) -> EntityQualityResult:
    from app.models.workforce import WorkOrder, WorkOrderAssignment, WorkOrderNote

    wo = db.get(WorkOrder, coerce_uuid(work_order_id))
    if not wo:
        return EntityQualityResult("work_order", work_order_id, 0.0, {}, ["work_order_not_found"])

    s: dict[str, float] = {}
    s["title"] = 1.0 if wo.title and len(wo.title.strip()) > 5 else 0.0
    s["description"] = min(1.0, len((wo.description or "").strip()) / 30)
    s["status"] = 1.0 if wo.status else 0.0
    s["priority"] = 1.0 if wo.priority else 0.0
    s["work_type"] = 1.0 if wo.work_type else 0.0
    s["assignee"] = 1.0 if wo.assigned_to_person_id else 0.0
    s["schedule"] = 1.0 if wo.scheduled_start else 0.0
    s["duration_estimate"] = 1.0 if wo.estimated_duration_minutes else 0.0
    s["skills"] = 1.0 if wo.required_skills else 0.0

    note_count = db.query(func.count(WorkOrderNote.id)).filter(WorkOrderNote.work_order_id == wo.id).scalar() or 0
    s["notes"] = min(1.0, note_count / 1)

    assignment_count = (
        db.query(func.count(WorkOrderAssignment.id)).filter(WorkOrderAssignment.work_order_id == wo.id).scalar() or 0
    )
    s["crew_assigned"] = min(1.0, assignment_count / 1)

    weights = {
        "title": 0.05,
        "description": 0.10,
        "status": 0.05,
        "priority": 0.05,
        "work_type": 0.08,
        "assignee": 0.15,
        "schedule": 0.15,
        "duration_estimate": 0.10,
        "skills": 0.07,
        "notes": 0.10,
        "crew_assigned": 0.10,
    }
    return EntityQualityResult("work_order", str(wo.id), _weighted_score(s, weights), s, _missing(s))


# ---------------------------------------------------------------------------
# Campaign
# ---------------------------------------------------------------------------


def score_campaign_quality(db: Session, campaign_id: str) -> EntityQualityResult:
    from app.models.crm.campaign import Campaign, CampaignRecipient

    campaign = db.get(Campaign, coerce_uuid(campaign_id))
    if not campaign:
        return EntityQualityResult("campaign", campaign_id, 0.0, {}, ["campaign_not_found"])

    s: dict[str, float] = {}
    s["name"] = 1.0 if campaign.name and len(campaign.name.strip()) > 3 else 0.0
    s["channel"] = 1.0 if campaign.channel else 0.0
    s["status"] = 1.0 if campaign.status else 0.0
    s["subject"] = 1.0 if campaign.subject and len(campaign.subject.strip()) > 3 else 0.0

    has_body = bool(
        (campaign.body_html and len(campaign.body_html.strip()) > 20)
        or (campaign.body_text and len(campaign.body_text.strip()) > 20)
        or campaign.whatsapp_template_name
    )
    s["content"] = 1.0 if has_body else 0.0

    s["sender_info"] = 1.0 if (campaign.from_email or campaign.campaign_sender_id) else 0.0
    s["schedule"] = 1.0 if campaign.scheduled_at else 0.0
    s["audience"] = 1.0 if campaign.segment_filter else 0.0

    recipient_count = (
        db.query(func.count(CampaignRecipient.id)).filter(CampaignRecipient.campaign_id == campaign.id).scalar() or 0
    )
    s["recipients"] = min(1.0, recipient_count / 5)

    weights = {
        "name": 0.05,
        "channel": 0.05,
        "status": 0.05,
        "subject": 0.10,
        "content": 0.25,
        "sender_info": 0.10,
        "schedule": 0.05,
        "audience": 0.10,
        "recipients": 0.25,
    }
    return EntityQualityResult("campaign", str(campaign.id), _weighted_score(s, weights), s, _missing(s))


# ---------------------------------------------------------------------------
# Vendor Quote
# ---------------------------------------------------------------------------


def score_vendor_quote_quality(db: Session, quote_id: str) -> EntityQualityResult:
    from app.models.vendor import ProjectQuote, QuoteLineItem, Vendor

    quote = db.get(ProjectQuote, coerce_uuid(quote_id))
    if not quote:
        return EntityQualityResult("vendor_quote", quote_id, 0.0, {}, ["quote_not_found"])

    s: dict[str, float] = {}
    s["status"] = 1.0 if quote.status else 0.0
    s["vendor"] = 1.0 if quote.vendor_id else 0.0
    s["project"] = 1.0 if quote.project_id else 0.0
    s["total"] = 1.0 if quote.total and float(quote.total) > 0 else 0.0
    s["validity"] = 1.0 if quote.valid_from and quote.valid_until else 0.0

    line_count = (
        db.query(func.count(QuoteLineItem.id))
        .filter(QuoteLineItem.quote_id == quote.id, QuoteLineItem.is_active.is_(True))
        .scalar()
        or 0
    )
    s["line_items"] = min(1.0, line_count / 2)

    if quote.vendor_id:
        vendor = db.get(Vendor, quote.vendor_id)
        s["vendor_contact"] = 1.0 if vendor and (vendor.contact_email or vendor.contact_phone) else 0.0
    else:
        s["vendor_contact"] = 0.0

    s["review"] = 1.0 if quote.reviewed_at else 0.0

    weights = {
        "status": 0.05,
        "vendor": 0.10,
        "project": 0.10,
        "total": 0.15,
        "validity": 0.10,
        "line_items": 0.25,
        "vendor_contact": 0.10,
        "review": 0.15,
    }
    return EntityQualityResult("vendor_quote", str(quote.id), _weighted_score(s, weights), s, _missing(s))


# ---------------------------------------------------------------------------
# Subscriber / Customer
# ---------------------------------------------------------------------------


def score_subscriber_quality(db: Session, subscriber_id: str) -> EntityQualityResult:
    from app.models.crm.conversation import Conversation
    from app.models.person import Person
    from app.models.subscriber import Subscriber
    from app.models.tickets import Ticket

    sub = db.get(Subscriber, coerce_uuid(subscriber_id))
    if not sub:
        return EntityQualityResult("subscriber", subscriber_id, 0.0, {}, ["subscriber_not_found"])

    s: dict[str, float] = {}
    s["status"] = 1.0 if sub.status else 0.0
    s["person"] = 1.0 if sub.person_id else 0.0
    s["service_plan"] = 1.0 if sub.service_plan else 0.0
    s["service_address"] = 1.0 if sub.service_address_line1 else 0.0
    s["subscriber_number"] = 1.0 if sub.subscriber_number else 0.0

    # Contact completeness
    if sub.person_id:
        person = db.get(Person, sub.person_id)
        if person:
            contact_fields = [person.email, person.phone, person.display_name]
            s["contact_info"] = sum(1.0 for f in contact_fields if f) / len(contact_fields)
        else:
            s["contact_info"] = 0.0
    else:
        s["contact_info"] = 0.0

    # Activity signals
    ticket_count = db.query(func.count(Ticket.id)).filter(Ticket.subscriber_id == sub.id).scalar() or 0
    s["ticket_history"] = min(1.0, ticket_count / 2)

    conv_count = 0
    if sub.person_id:
        conv_count = db.query(func.count(Conversation.id)).filter(Conversation.person_id == sub.person_id).scalar() or 0
    s["conversation_history"] = min(1.0, conv_count / 2)

    weights = {
        "status": 0.05,
        "person": 0.15,
        "service_plan": 0.10,
        "service_address": 0.10,
        "subscriber_number": 0.05,
        "contact_info": 0.20,
        "ticket_history": 0.15,
        "conversation_history": 0.20,
    }
    return EntityQualityResult("subscriber", str(sub.id), _weighted_score(s, weights), s, _missing(s))


# ---------------------------------------------------------------------------
# Performance Snapshot (for AI coach readiness)
# ---------------------------------------------------------------------------


def score_performance_snapshot_quality(
    db: Session, person_id: str, period_start: str | None = None
) -> EntityQualityResult:
    from app.models.performance import AgentPerformanceSnapshot

    query = db.query(AgentPerformanceSnapshot).filter(AgentPerformanceSnapshot.person_id == coerce_uuid(person_id))
    if period_start:
        query = query.filter(AgentPerformanceSnapshot.score_period_start == period_start)
    snap = query.order_by(AgentPerformanceSnapshot.created_at.desc()).first()

    if not snap:
        return EntityQualityResult("performance_snapshot", person_id, 0.0, {}, ["no_snapshot"])

    s: dict[str, float] = {}
    domain_scores = snap.domain_scores_json or {}
    domains_with_data = sum(1 for v in domain_scores.values() if v is not None and float(v) > 0)
    s["domain_coverage"] = min(1.0, domains_with_data / 3)  # 3+ domains = full score
    s["composite_score"] = 1.0 if snap.composite_score is not None else 0.0
    s["team_context"] = 1.0 if snap.team_id else 0.0
    s["weights"] = 1.0 if snap.weights_json else 0.0

    weights = {
        "domain_coverage": 0.50,
        "composite_score": 0.20,
        "team_context": 0.15,
        "weights": 0.15,
    }
    return EntityQualityResult("performance_snapshot", str(snap.id), _weighted_score(s, weights), s, _missing(s))
