"""Domain-level data quality aggregation for the dashboard and API.

Scans entities per domain, scores each, and returns aggregate health reports.
Designed for batch operation — queries are optimized to fetch ID lists first,
then score individually (avoiding loading full ORM objects in bulk).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.services.data_quality.scoring import (
    EntityQualityResult,
    score_campaign_quality,
    score_conversation_quality,
    score_project_quality,
    score_subscriber_quality,
    score_ticket_quality,
    score_vendor_quote_quality,
    score_work_order_quality,
)


@dataclass
class DomainHealthReport:
    """Aggregated data quality for one domain."""

    domain: str
    label: str
    entity_count: int
    avg_quality: float
    pct_above_threshold: float  # % scoring >= 0.3
    pct_high_quality: float  # % scoring >= 0.7
    top_missing_fields: list[tuple[str, int]]  # (field_name, count) top 5
    sample_worst: list[EntityQualityResult]  # up to 5 lowest-scoring entities

    def avg_pct(self) -> int:
        return round(self.avg_quality * 100)


def _aggregate(
    results: list[EntityQualityResult],
    domain: str,
    label: str,
) -> DomainHealthReport:
    if not results:
        return DomainHealthReport(
            domain=domain,
            label=label,
            entity_count=0,
            avg_quality=0.0,
            pct_above_threshold=0.0,
            pct_high_quality=0.0,
            top_missing_fields=[],
            sample_worst=[],
        )

    total = len(results)
    avg = sum(r.score for r in results) / total
    above = sum(1 for r in results if r.score >= 0.3)
    high = sum(1 for r in results if r.score >= 0.7)

    # Missing field frequency
    field_counts: dict[str, int] = {}
    for r in results:
        for f in r.missing_fields:
            field_counts[f] = field_counts.get(f, 0) + 1
    top_missing = sorted(field_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    # Worst samples
    sorted_results = sorted(results, key=lambda r: r.score)
    sample_worst = sorted_results[:5]

    return DomainHealthReport(
        domain=domain,
        label=label,
        entity_count=total,
        avg_quality=round(avg, 3),
        pct_above_threshold=round(above / total * 100, 1) if total else 0.0,
        pct_high_quality=round(high / total * 100, 1) if total else 0.0,
        top_missing_fields=top_missing,
        sample_worst=sample_worst,
    )


# ---------------------------------------------------------------------------
# Per-domain report builders
# ---------------------------------------------------------------------------


def _ticket_report(db: Session, *, limit: int = 200) -> DomainHealthReport:
    from app.models.tickets import Ticket

    ids = db.query(Ticket.id).filter(Ticket.is_active.is_(True)).order_by(Ticket.updated_at.desc()).limit(limit).all()
    results = [score_ticket_quality(db, str(tid)) for (tid,) in ids]
    return _aggregate(results, "tickets", "Support Tickets")


def _conversation_report(db: Session, *, limit: int = 200) -> DomainHealthReport:
    from app.models.crm.conversation import Conversation

    ids = (
        db.query(Conversation.id)
        .filter(Conversation.is_active.is_(True))
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .all()
    )
    results = [score_conversation_quality(db, str(cid)) for (cid,) in ids]
    return _aggregate(results, "conversations", "CRM Conversations")


def _project_report(db: Session, *, limit: int = 200) -> DomainHealthReport:
    from app.models.projects import Project

    ids = (
        db.query(Project.id).filter(Project.is_active.is_(True)).order_by(Project.updated_at.desc()).limit(limit).all()
    )
    results = [score_project_quality(db, str(pid)) for (pid,) in ids]
    return _aggregate(results, "projects", "Projects")


def _work_order_report(db: Session, *, limit: int = 200) -> DomainHealthReport:
    from app.models.workforce import WorkOrder

    ids = (
        db.query(WorkOrder.id)
        .filter(WorkOrder.is_active.is_(True))
        .order_by(WorkOrder.updated_at.desc())
        .limit(limit)
        .all()
    )
    results = [score_work_order_quality(db, str(wid)) for (wid,) in ids]
    return _aggregate(results, "work_orders", "Work Orders")


def _campaign_report(db: Session, *, limit: int = 200) -> DomainHealthReport:
    from app.models.crm.campaign import Campaign

    ids = (
        db.query(Campaign.id)
        .filter(Campaign.is_active.is_(True))
        .order_by(Campaign.updated_at.desc())
        .limit(limit)
        .all()
    )
    results = [score_campaign_quality(db, str(cid)) for (cid,) in ids]
    return _aggregate(results, "campaigns", "Campaigns")


def _vendor_quote_report(db: Session, *, limit: int = 200) -> DomainHealthReport:
    from app.models.vendor import ProjectQuote

    ids = (
        db.query(ProjectQuote.id)
        .filter(ProjectQuote.is_active.is_(True))
        .order_by(ProjectQuote.updated_at.desc())
        .limit(limit)
        .all()
    )
    results = [score_vendor_quote_quality(db, str(qid)) for (qid,) in ids]
    return _aggregate(results, "vendor_quotes", "Vendor Quotes")


def _subscriber_report(db: Session, *, limit: int = 200) -> DomainHealthReport:
    from app.models.subscriber import Subscriber

    ids = (
        db.query(Subscriber.id)
        .filter(Subscriber.is_active.is_(True))
        .order_by(Subscriber.updated_at.desc())
        .limit(limit)
        .all()
    )
    results = [score_subscriber_quality(db, str(sid)) for (sid,) in ids]
    return _aggregate(results, "subscribers", "Subscribers")


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

DOMAIN_REPORTERS: dict[str, Any] = {
    "tickets": _ticket_report,
    "conversations": _conversation_report,
    "projects": _project_report,
    "work_orders": _work_order_report,
    "campaigns": _campaign_report,
    "vendor_quotes": _vendor_quote_report,
    "subscribers": _subscriber_report,
}


def domain_health_report(db: Session, domain: str, *, limit: int = 200) -> DomainHealthReport:
    """Generate a data quality report for a single domain."""
    reporter = DOMAIN_REPORTERS.get(domain)
    if not reporter:
        raise ValueError(f"Unknown domain: {domain}. Valid: {', '.join(DOMAIN_REPORTERS)}")
    return reporter(db, limit=limit)


def all_domains_health(db: Session, *, limit: int = 200) -> list[DomainHealthReport]:
    """Generate data quality reports for all domains."""
    return [reporter(db, limit=limit) for reporter in DOMAIN_REPORTERS.values()]


def domain_entity_list(
    db: Session,
    domain: str,
    *,
    limit: int = 50,
    offset: int = 0,
    sort: str = "worst",
) -> tuple[list[EntityQualityResult], int]:
    """Return a paginated list of entity quality results for drill-down views.

    Returns (results, total_count).
    """
    reporter = DOMAIN_REPORTERS.get(domain)
    if not reporter:
        return [], 0

    # We need the full results list — call the reporter's underlying logic
    full = _get_scored_entities(db, domain, limit=500)
    if sort == "worst":
        full.sort(key=lambda r: r.score)
    elif sort == "best":
        full.sort(key=lambda r: r.score, reverse=True)

    total = len(full)
    page = full[offset : offset + limit]
    return page, total


def _get_scored_entities(db: Session, domain: str, *, limit: int = 500) -> list[EntityQualityResult]:
    """Score entities for a domain and return the raw result list."""
    from app.models.crm.campaign import Campaign
    from app.models.crm.conversation import Conversation
    from app.models.projects import Project
    from app.models.subscriber import Subscriber
    from app.models.tickets import Ticket
    from app.models.vendor import ProjectQuote
    from app.models.workforce import WorkOrder

    model_map: dict[str, Any] = {
        "tickets": (Ticket, score_ticket_quality),
        "conversations": (Conversation, score_conversation_quality),
        "projects": (Project, score_project_quality),
        "work_orders": (WorkOrder, score_work_order_quality),
        "campaigns": (Campaign, score_campaign_quality),
        "vendor_quotes": (ProjectQuote, score_vendor_quote_quality),
        "subscribers": (Subscriber, score_subscriber_quality),
    }
    entry = model_map.get(domain)
    if not entry:
        return []

    model_cls, scorer = entry
    ids = (
        db.query(model_cls.id)
        .filter(model_cls.is_active.is_(True))
        .order_by(model_cls.updated_at.desc())
        .limit(limit)
        .all()
    )
    return [scorer(db, str(eid)) for (eid,) in ids]
