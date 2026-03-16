"""Subscriber report service functions for reports 1-4."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.crm.enums import LeadStatus
from app.models.crm.sales import Lead
from app.models.person import Person
from app.models.projects import Project
from app.models.sales_order import SalesOrder, SalesOrderStatus
from app.models.subscriber import Subscriber, SubscriberStatus
from app.models.tickets import Ticket, TicketSlaEvent, TicketStatus
from app.models.workforce import WorkOrder, WorkOrderStatus

# =====================================================================
# Report 1: Subscriber Overview
# =====================================================================


def overview_kpis(
    db: Session, start_dt: datetime, end_dt: datetime
) -> dict:
    """5 KPI cards for subscriber overview."""
    active_count = db.scalar(
        select(func.count(Subscriber.id)).where(
            Subscriber.is_active.is_(True),
            Subscriber.status == SubscriberStatus.active,
        )
    ) or 0

    activations = db.scalar(
        select(func.count(Subscriber.id)).where(
            Subscriber.is_active.is_(True),
            Subscriber.activated_at >= start_dt,
            Subscriber.activated_at <= end_dt,
        )
    ) or 0
    terminations = db.scalar(
        select(func.count(Subscriber.id)).where(
            Subscriber.is_active.is_(True),
            Subscriber.terminated_at >= start_dt,
            Subscriber.terminated_at <= end_dt,
        )
    ) or 0
    net_growth = activations - terminations

    suspended_count = db.scalar(
        select(func.count(Subscriber.id)).where(
            Subscriber.is_active.is_(True),
            Subscriber.status == SubscriberStatus.suspended,
        )
    ) or 0
    total_subs = db.scalar(
        select(func.count(Subscriber.id)).where(Subscriber.is_active.is_(True))
    ) or 0
    suspended_pct = round(suspended_count / total_subs * 100, 1) if total_subs > 0 else 0

    ticket_count = db.scalar(
        select(func.count(Ticket.id)).where(
            Ticket.is_active.is_(True),
            Ticket.subscriber_id.isnot(None),
            Ticket.created_at >= start_dt,
            Ticket.created_at <= end_dt,
        )
    ) or 0
    avg_tickets = round(ticket_count / active_count, 2) if active_count > 0 else 0

    region_count = db.scalar(
        select(func.count(func.distinct(Subscriber.service_region))).where(
            Subscriber.is_active.is_(True),
            Subscriber.service_region.isnot(None),
        )
    ) or 0

    return {
        "active_subscribers": active_count,
        "net_growth": net_growth,
        "activations": activations,
        "terminations": terminations,
        "suspended_count": suspended_count,
        "suspended_pct": suspended_pct,
        "avg_tickets_per_sub": avg_tickets,
        "regions_covered": region_count,
    }


def overview_growth_trend(
    db: Session, start_dt: datetime, end_dt: datetime
) -> list[dict]:
    """Daily activations vs terminations."""
    act_rows = db.execute(
        select(
            func.date_trunc("day", Subscriber.activated_at).label("day"),
            func.count(Subscriber.id),
        )
        .where(
            Subscriber.is_active.is_(True),
            Subscriber.activated_at >= start_dt,
            Subscriber.activated_at <= end_dt,
        )
        .group_by("day")
        .order_by("day")
    ).all()

    term_rows = db.execute(
        select(
            func.date_trunc("day", Subscriber.terminated_at).label("day"),
            func.count(Subscriber.id),
        )
        .where(
            Subscriber.is_active.is_(True),
            Subscriber.terminated_at >= start_dt,
            Subscriber.terminated_at <= end_dt,
        )
        .group_by("day")
        .order_by("day")
    ).all()

    act_map = {row[0].strftime("%Y-%m-%d"): row[1] for row in act_rows if row[0]}
    term_map = {row[0].strftime("%Y-%m-%d"): row[1] for row in term_rows if row[0]}
    all_dates = sorted(set(act_map.keys()) | set(term_map.keys()))

    return [
        {
            "date": d,
            "activations": act_map.get(d, 0),
            "terminations": term_map.get(d, 0),
        }
        for d in all_dates
    ]


def overview_status_distribution(db: Session) -> dict[str, int]:
    """Subscriber counts by status."""
    rows = db.execute(
        select(Subscriber.status, func.count(Subscriber.id))
        .where(Subscriber.is_active.is_(True))
        .group_by(Subscriber.status)
    ).all()
    return {
        (status.value if status else "unknown"): count
        for status, count in rows
    }


def overview_plan_distribution(db: Session, limit: int = 10) -> list[dict]:
    """Top service plans by subscriber count."""
    rows = db.execute(
        select(Subscriber.service_plan, func.count(Subscriber.id).label("cnt"))
        .where(
            Subscriber.is_active.is_(True),
            Subscriber.status == SubscriberStatus.active,
            Subscriber.service_plan.isnot(None),
        )
        .group_by(Subscriber.service_plan)
        .order_by(func.count(Subscriber.id).desc())
        .limit(limit)
    ).all()
    return [{"plan": row[0], "count": row[1]} for row in rows]


def overview_regional_breakdown(
    db: Session, start_dt: datetime, end_dt: datetime
) -> list[dict]:
    """Regional breakdown table."""
    regions = db.execute(
        select(
            Subscriber.service_region,
            func.count(Subscriber.id).filter(Subscriber.status == SubscriberStatus.active).label("active"),
            func.count(Subscriber.id).filter(Subscriber.status == SubscriberStatus.suspended).label("suspended"),
            func.count(Subscriber.id).filter(Subscriber.status == SubscriberStatus.terminated).label("terminated"),
            func.count(Subscriber.id).filter(
                Subscriber.activated_at >= start_dt,
                Subscriber.activated_at <= end_dt,
            ).label("new_in_period"),
        )
        .where(Subscriber.is_active.is_(True), Subscriber.service_region.isnot(None))
        .group_by(Subscriber.service_region)
        .order_by(func.count(Subscriber.id).desc())
    ).all()

    # Ticket counts per region
    region_names = [r[0] for r in regions]
    ticket_rows = db.execute(
        select(Subscriber.service_region, func.count(Ticket.id))
        .join(Ticket, Ticket.subscriber_id == Subscriber.id)
        .where(
            Subscriber.service_region.in_(region_names),
            Ticket.is_active.is_(True),
            Ticket.created_at >= start_dt,
            Ticket.created_at <= end_dt,
        )
        .group_by(Subscriber.service_region)
    ).all() if region_names else []
    ticket_map = {row[0]: row[1] for row in ticket_rows}

    return [
        {
            "region": row[0],
            "active": row[1],
            "suspended": row[2],
            "terminated": row[3],
            "new_in_period": row[4],
            "ticket_count": ticket_map.get(row[0], 0),
        }
        for row in regions
    ]


def overview_filter_options(db: Session) -> dict:
    """Dropdown options for overview filters."""
    regions = db.scalars(
        select(func.distinct(Subscriber.service_region))
        .where(Subscriber.is_active.is_(True), Subscriber.service_region.isnot(None))
        .order_by(Subscriber.service_region)
    ).all()
    plans = db.scalars(
        select(func.distinct(Subscriber.service_plan))
        .where(Subscriber.is_active.is_(True), Subscriber.service_plan.isnot(None))
        .order_by(Subscriber.service_plan)
    ).all()
    return {"regions": regions, "plans": plans}


# =====================================================================
# Report 2: Subscriber Lifecycle
# =====================================================================


def lifecycle_kpis(
    db: Session, start_dt: datetime, end_dt: datetime
) -> dict:
    """5 KPI cards for lifecycle report."""
    # Leads created in period
    leads_created = db.scalar(
        select(func.count(Lead.id)).where(
            Lead.is_active.is_(True),
            Lead.created_at >= start_dt,
            Lead.created_at <= end_dt,
        )
    ) or 0
    leads_won = db.scalar(
        select(func.count(Lead.id)).where(
            Lead.is_active.is_(True),
            Lead.status == LeadStatus.won,
            Lead.closed_at >= start_dt,
            Lead.closed_at <= end_dt,
        )
    ) or 0
    conversion_rate = round(leads_won / leads_created * 100, 1) if leads_created > 0 else 0

    # Avg days to convert
    avg_days_result = db.scalar(
        select(
            func.avg(
                func.extract("epoch", Subscriber.activated_at - Person.created_at) / 86400
            )
        )
        .join(Person, Person.id == Subscriber.person_id)
        .where(
            Subscriber.is_active.is_(True),
            Subscriber.activated_at >= start_dt,
            Subscriber.activated_at <= end_dt,
            Subscriber.activated_at.isnot(None),
        )
    )
    avg_days_to_convert = round(float(avg_days_result), 1) if avg_days_result else 0

    # Churn rate
    active_at_start = db.scalar(
        select(func.count(Subscriber.id)).where(
            Subscriber.is_active.is_(True),
            Subscriber.status.in_([SubscriberStatus.active, SubscriberStatus.suspended]),
            Subscriber.activated_at < start_dt,
        )
    ) or 0
    terminated_in_period = db.scalar(
        select(func.count(Subscriber.id)).where(
            Subscriber.is_active.is_(True),
            Subscriber.terminated_at >= start_dt,
            Subscriber.terminated_at <= end_dt,
        )
    ) or 0
    churn_rate = round(terminated_in_period / active_at_start * 100, 1) if active_at_start > 0 else 0

    # Pipeline value (open leads where person is already a subscriber)
    pipeline_value = db.scalar(
        select(func.coalesce(func.sum(Lead.estimated_value), 0))
        .join(Subscriber, Subscriber.person_id == Lead.person_id)
        .where(
            Lead.is_active.is_(True),
            Lead.status.notin_([LeadStatus.won, LeadStatus.lost]),
            Subscriber.is_active.is_(True),
            Subscriber.status == SubscriberStatus.active,
        )
    ) or Decimal("0")

    return {
        "conversion_rate": conversion_rate,
        "avg_days_to_convert": avg_days_to_convert,
        "churn_rate": churn_rate,
        "terminated_in_period": terminated_in_period,
        "pipeline_value": float(pipeline_value),
        "leads_won": leads_won,
    }


def lifecycle_funnel(db: Session) -> list[dict]:
    """Person counts by party_status: lead→contact→customer→subscriber."""
    rows = db.execute(
        select(Person.party_status, func.count(Person.id))
        .where(Person.is_active.is_(True))
        .group_by(Person.party_status)
    ).all()
    status_map = {
        (s.value if s else "unknown"): c for s, c in rows
    }
    order = ["lead", "contact", "customer", "subscriber"]
    return [
        {"stage": stage, "count": status_map.get(stage, 0)}
        for stage in order
    ]


def lifecycle_churn_trend(db: Session) -> list[dict]:
    """Monthly termination count over last 12 months."""
    from datetime import timedelta

    cutoff = datetime.now(UTC) - timedelta(days=365)
    rows = db.execute(
        select(
            func.date_trunc("month", Subscriber.terminated_at).label("month"),
            func.count(Subscriber.id),
        )
        .where(
            Subscriber.is_active.is_(True),
            Subscriber.terminated_at.isnot(None),
            Subscriber.terminated_at >= cutoff,
        )
        .group_by("month")
        .order_by("month")
    ).all()
    return [
        {"month": row[0].strftime("%Y-%m"), "count": row[1]}
        for row in rows
        if row[0]
    ]


def lifecycle_conversion_by_source(db: Session, start_dt: datetime, end_dt: datetime) -> list[dict]:
    """Per lead_source: total leads vs won."""
    rows = db.execute(
        select(
            Lead.lead_source,
            func.count(Lead.id).label("total"),
            func.count(Lead.id).filter(Lead.status == LeadStatus.won).label("won"),
        )
        .where(
            Lead.is_active.is_(True),
            Lead.lead_source.isnot(None),
            Lead.created_at >= start_dt,
            Lead.created_at <= end_dt,
        )
        .group_by(Lead.lead_source)
        .order_by(func.count(Lead.id).desc())
    ).all()
    return [
        {"source": row[0], "total": row[1], "won": row[2]}
        for row in rows
    ]


def lifecycle_recent_churns(db: Session, limit: int = 20) -> list[dict]:
    """Recently terminated subscribers."""
    subs = db.execute(
        select(
            Subscriber.subscriber_number,
            Subscriber.service_plan,
            Subscriber.service_region,
            Subscriber.activated_at,
            Subscriber.terminated_at,
            Person.first_name,
            Person.last_name,
            Person.display_name,
        )
        .join(Person, Person.id == Subscriber.person_id)
        .where(
            Subscriber.is_active.is_(True),
            Subscriber.terminated_at.isnot(None),
        )
        .order_by(Subscriber.terminated_at.desc())
        .limit(limit)
    ).all()

    results = []
    for row in subs:
        name = row.display_name or f"{row.first_name or ''} {row.last_name or ''}".strip() or "Unknown"
        tenure = 0
        if row.activated_at and row.terminated_at:
            tenure = (row.terminated_at - row.activated_at).days
        results.append({
            "name": name,
            "subscriber_number": row.subscriber_number,
            "plan": row.service_plan or "",
            "region": row.service_region or "",
            "activated_at": row.activated_at.strftime("%Y-%m-%d") if row.activated_at else "",
            "terminated_at": row.terminated_at.strftime("%Y-%m-%d") if row.terminated_at else "",
            "tenure_days": tenure,
        })
    return results


def lifecycle_longest_tenure(db: Session, limit: int = 10) -> list[dict]:
    """Top subscribers by tenure (active only)."""
    subs = db.execute(
        select(
            Subscriber.subscriber_number,
            Subscriber.service_plan,
            Subscriber.service_region,
            Subscriber.activated_at,
            Person.first_name,
            Person.last_name,
            Person.display_name,
        )
        .join(Person, Person.id == Subscriber.person_id)
        .where(
            Subscriber.is_active.is_(True),
            Subscriber.status == SubscriberStatus.active,
            Subscriber.activated_at.isnot(None),
        )
        .order_by(Subscriber.activated_at.asc())
        .limit(limit)
    ).all()

    now = datetime.now(UTC)
    results = []
    for row in subs:
        name = row.display_name or f"{row.first_name or ''} {row.last_name or ''}".strip() or "Unknown"
        tenure = (now - row.activated_at).days if row.activated_at else 0
        results.append({
            "name": name,
            "subscriber_number": row.subscriber_number,
            "plan": row.service_plan or "",
            "region": row.service_region or "",
            "activated_at": row.activated_at.strftime("%Y-%m-%d") if row.activated_at else "",
            "tenure_days": tenure,
        })
    return results


# =====================================================================
# Report 3: Service Quality
# =====================================================================


def service_quality_kpis(
    db: Session, start_dt: datetime, end_dt: datetime
) -> dict:
    """5 KPI cards for service quality."""
    open_statuses = [TicketStatus.new, TicketStatus.open, TicketStatus.pending]

    subs_with_open_tickets = db.scalar(
        select(func.count(func.distinct(Ticket.subscriber_id))).where(
            Ticket.is_active.is_(True),
            Ticket.subscriber_id.isnot(None),
            Ticket.status.in_(open_statuses),
        )
    ) or 0

    # Avg resolution time
    avg_res = db.scalar(
        select(
            func.avg(
                func.extract("epoch", Ticket.resolved_at - Ticket.created_at) / 3600
            )
        ).where(
            Ticket.is_active.is_(True),
            Ticket.subscriber_id.isnot(None),
            Ticket.resolved_at.isnot(None),
            Ticket.created_at >= start_dt,
            Ticket.created_at <= end_dt,
        )
    )
    avg_resolution_hrs = round(float(avg_res), 1) if avg_res else 0

    # Repeat contact rate
    sub_ticket_counts = db.execute(
        select(Ticket.subscriber_id, func.count(Ticket.id).label("cnt"))
        .where(
            Ticket.is_active.is_(True),
            Ticket.subscriber_id.isnot(None),
            Ticket.created_at >= start_dt,
            Ticket.created_at <= end_dt,
        )
        .group_by(Ticket.subscriber_id)
    ).all()
    total_subs_with_tickets = len(sub_ticket_counts)
    repeat_subs = sum(1 for _, cnt in sub_ticket_counts if cnt >= 2)
    repeat_rate = round(repeat_subs / total_subs_with_tickets * 100, 1) if total_subs_with_tickets > 0 else 0

    # Active work orders
    active_wo = db.scalar(
        select(func.count(WorkOrder.id)).where(
            WorkOrder.is_active.is_(True),
            WorkOrder.subscriber_id.isnot(None),
            WorkOrder.status.notin_([WorkOrderStatus.completed, WorkOrderStatus.canceled]),
        )
    ) or 0

    # SLA compliance
    total_sla = db.scalar(
        select(func.count(TicketSlaEvent.id)).where(
            TicketSlaEvent.expected_at.isnot(None),
            TicketSlaEvent.actual_at.isnot(None),
            TicketSlaEvent.created_at >= start_dt,
            TicketSlaEvent.created_at <= end_dt,
        )
    ) or 0
    met_sla = db.scalar(
        select(func.count(TicketSlaEvent.id)).where(
            TicketSlaEvent.expected_at.isnot(None),
            TicketSlaEvent.actual_at.isnot(None),
            TicketSlaEvent.actual_at <= TicketSlaEvent.expected_at,
            TicketSlaEvent.created_at >= start_dt,
            TicketSlaEvent.created_at <= end_dt,
        )
    ) or 0
    sla_compliance = round(met_sla / total_sla * 100, 1) if total_sla > 0 else 0

    return {
        "subs_with_open_tickets": subs_with_open_tickets,
        "avg_resolution_hrs": avg_resolution_hrs,
        "repeat_contact_rate": repeat_rate,
        "active_work_orders": active_wo,
        "sla_compliance": sla_compliance,
    }


def service_quality_tickets_by_type(
    db: Session, start_dt: datetime, end_dt: datetime
) -> dict[str, int]:
    """Ticket type distribution."""
    rows = db.execute(
        select(Ticket.ticket_type, func.count(Ticket.id))
        .where(
            Ticket.is_active.is_(True),
            Ticket.subscriber_id.isnot(None),
            Ticket.created_at >= start_dt,
            Ticket.created_at <= end_dt,
        )
        .group_by(Ticket.ticket_type)
    ).all()
    return {(t or "unclassified"): c for t, c in rows}


def service_quality_wo_by_type(
    db: Session, start_dt: datetime, end_dt: datetime
) -> dict[str, int]:
    """Work order type distribution."""
    rows = db.execute(
        select(WorkOrder.work_type, func.count(WorkOrder.id))
        .where(
            WorkOrder.is_active.is_(True),
            WorkOrder.subscriber_id.isnot(None),
            WorkOrder.created_at >= start_dt,
            WorkOrder.created_at <= end_dt,
        )
        .group_by(WorkOrder.work_type)
    ).all()
    return {(t.value if t else "other"): c for t, c in rows}


def service_quality_weekly_trend(
    db: Session, start_dt: datetime, end_dt: datetime
) -> list[dict]:
    """Weekly tickets created vs resolved."""
    created_rows = db.execute(
        select(
            func.date_trunc("week", Ticket.created_at).label("week"),
            func.count(Ticket.id),
        )
        .where(
            Ticket.is_active.is_(True),
            Ticket.subscriber_id.isnot(None),
            Ticket.created_at >= start_dt,
            Ticket.created_at <= end_dt,
        )
        .group_by("week")
        .order_by("week")
    ).all()

    resolved_rows = db.execute(
        select(
            func.date_trunc("week", Ticket.resolved_at).label("week"),
            func.count(Ticket.id),
        )
        .where(
            Ticket.is_active.is_(True),
            Ticket.subscriber_id.isnot(None),
            Ticket.resolved_at >= start_dt,
            Ticket.resolved_at <= end_dt,
        )
        .group_by("week")
        .order_by("week")
    ).all()

    created_map = {row[0].strftime("%Y-%m-%d"): row[1] for row in created_rows if row[0]}
    resolved_map = {row[0].strftime("%Y-%m-%d"): row[1] for row in resolved_rows if row[0]}
    all_weeks = sorted(set(created_map.keys()) | set(resolved_map.keys()))

    return [
        {
            "week": w,
            "created": created_map.get(w, 0),
            "resolved": resolved_map.get(w, 0),
        }
        for w in all_weeks
    ]


def service_quality_high_maintenance(
    db: Session, start_dt: datetime, end_dt: datetime, limit: int = 20
) -> list[dict]:
    """Subscribers ranked by total tickets + WOs + projects."""
    # Get ticket counts per subscriber
    ticket_counts = db.execute(
        select(Ticket.subscriber_id, func.count(Ticket.id))
        .where(
            Ticket.is_active.is_(True),
            Ticket.subscriber_id.isnot(None),
            Ticket.created_at >= start_dt,
            Ticket.created_at <= end_dt,
        )
        .group_by(Ticket.subscriber_id)
    ).all()
    ticket_map = {row[0]: row[1] for row in ticket_counts}

    wo_counts = db.execute(
        select(WorkOrder.subscriber_id, func.count(WorkOrder.id))
        .where(
            WorkOrder.is_active.is_(True),
            WorkOrder.subscriber_id.isnot(None),
            WorkOrder.created_at >= start_dt,
            WorkOrder.created_at <= end_dt,
        )
        .group_by(WorkOrder.subscriber_id)
    ).all()
    wo_map = {row[0]: row[1] for row in wo_counts}

    proj_counts = db.execute(
        select(Project.subscriber_id, func.count(Project.id))
        .where(
            Project.is_active.is_(True),
            Project.subscriber_id.isnot(None),
            Project.created_at >= start_dt,
            Project.created_at <= end_dt,
        )
        .group_by(Project.subscriber_id)
    ).all()
    proj_map = {row[0]: row[1] for row in proj_counts}

    all_sub_ids = set(ticket_map.keys()) | set(wo_map.keys()) | set(proj_map.keys())
    if not all_sub_ids:
        return []

    ranked = []
    for sid in all_sub_ids:
        t = ticket_map.get(sid, 0)
        w = wo_map.get(sid, 0)
        p = proj_map.get(sid, 0)
        ranked.append((sid, t, w, p, t + w + p))
    ranked.sort(key=lambda x: -x[4])
    ranked = ranked[:limit]

    sub_ids = [r[0] for r in ranked]
    subs = db.execute(
        select(
            Subscriber.id,
            Subscriber.subscriber_number,
            Subscriber.service_plan,
            Subscriber.service_region,
            Person.display_name,
            Person.first_name,
            Person.last_name,
        )
        .join(Person, Person.id == Subscriber.person_id)
        .where(Subscriber.id.in_(sub_ids))
    ).all()
    sub_map = {s[0]: s for s in subs}

    results = []
    for sid, tickets, wos, projects, total in ranked:
        s = sub_map.get(sid)
        if not s:
            continue
        name = s.display_name or f"{s.first_name or ''} {s.last_name or ''}".strip() or "Unknown"
        results.append({
            "name": name,
            "subscriber_number": s.subscriber_number,
            "region": s.service_region or "",
            "plan": s.service_plan or "",
            "tickets": tickets,
            "work_orders": wos,
            "projects": projects,
            "total": total,
        })
    return results


def service_quality_regional(
    db: Session, start_dt: datetime, end_dt: datetime
) -> list[dict]:
    """Regional service quality metrics."""
    regions = db.scalars(
        select(func.distinct(Subscriber.service_region))
        .where(Subscriber.is_active.is_(True), Subscriber.service_region.isnot(None))
    ).all()

    if not regions:
        return []

    results = []
    for region in regions:
        active_in_region = db.scalar(
            select(func.count(Subscriber.id)).where(
                Subscriber.is_active.is_(True),
                Subscriber.status == SubscriberStatus.active,
                Subscriber.service_region == region,
            )
        ) or 0

        tickets_in_region = db.scalar(
            select(func.count(Ticket.id))
            .join(Subscriber, Subscriber.id == Ticket.subscriber_id)
            .where(
                Ticket.is_active.is_(True),
                Subscriber.service_region == region,
                Ticket.created_at >= start_dt,
                Ticket.created_at <= end_dt,
            )
        ) or 0

        avg_tickets = round(tickets_in_region / active_in_region, 2) if active_in_region > 0 else 0

        avg_res = db.scalar(
            select(func.avg(func.extract("epoch", Ticket.resolved_at - Ticket.created_at) / 3600))
            .join(Subscriber, Subscriber.id == Ticket.subscriber_id)
            .where(
                Ticket.is_active.is_(True),
                Ticket.resolved_at.isnot(None),
                Subscriber.service_region == region,
                Ticket.created_at >= start_dt,
                Ticket.created_at <= end_dt,
            )
        )
        avg_res_hrs = round(float(avg_res), 1) if avg_res else 0

        wo_count = db.scalar(
            select(func.count(WorkOrder.id))
            .join(Subscriber, Subscriber.id == WorkOrder.subscriber_id)
            .where(
                WorkOrder.is_active.is_(True),
                Subscriber.service_region == region,
                WorkOrder.created_at >= start_dt,
                WorkOrder.created_at <= end_dt,
            )
        ) or 0

        results.append({
            "region": region,
            "active_subscribers": active_in_region,
            "avg_tickets_per_sub": avg_tickets,
            "avg_resolution_hrs": avg_res_hrs,
            "wo_count": wo_count,
        })

    results.sort(key=lambda x: -x["active_subscribers"])
    return results


# =====================================================================
# Report 4: Revenue & Pipeline
# =====================================================================


def revenue_kpis(
    db: Session, start_dt: datetime, end_dt: datetime
) -> dict:
    """5 KPI cards for revenue report."""
    total_value = db.scalar(
        select(func.coalesce(func.sum(SalesOrder.total), 0)).where(
            SalesOrder.is_active.is_(True),
            SalesOrder.created_at >= start_dt,
            SalesOrder.created_at <= end_dt,
        )
    ) or Decimal("0")

    order_count = db.scalar(
        select(func.count(SalesOrder.id)).where(
            SalesOrder.is_active.is_(True),
            SalesOrder.created_at >= start_dt,
            SalesOrder.created_at <= end_dt,
        )
    ) or 0

    avg_value = float(total_value) / order_count if order_count > 0 else 0

    pipeline_value = db.scalar(
        select(func.coalesce(func.sum(Lead.estimated_value), 0)).where(
            Lead.is_active.is_(True),
            Lead.status.notin_([LeadStatus.won, LeadStatus.lost]),
        )
    ) or Decimal("0")

    total_paid = db.scalar(
        select(func.coalesce(func.sum(SalesOrder.amount_paid), 0)).where(
            SalesOrder.is_active.is_(True),
            SalesOrder.created_at >= start_dt,
            SalesOrder.created_at <= end_dt,
        )
    ) or Decimal("0")
    collection_rate = round(float(total_paid) / float(total_value) * 100, 1) if float(total_value) > 0 else 0

    pending_fulfillment = db.scalar(
        select(func.count(SalesOrder.id)).where(
            SalesOrder.is_active.is_(True),
            SalesOrder.status.in_([SalesOrderStatus.confirmed, SalesOrderStatus.paid]),
        )
    ) or 0

    return {
        "total_value": float(total_value),
        "order_count": order_count,
        "avg_value": round(avg_value, 2),
        "pipeline_value": float(pipeline_value),
        "collection_rate": collection_rate,
        "pending_fulfillment": pending_fulfillment,
    }


def revenue_monthly_trend(db: Session) -> list[dict]:
    """Monthly revenue over last 12 months."""
    from datetime import timedelta

    cutoff = datetime.now(UTC) - timedelta(days=365)
    rows = db.execute(
        select(
            func.date_trunc("month", SalesOrder.created_at).label("month"),
            func.coalesce(func.sum(SalesOrder.total), 0),
        )
        .where(
            SalesOrder.is_active.is_(True),
            SalesOrder.created_at >= cutoff,
        )
        .group_by("month")
        .order_by("month")
    ).all()
    return [
        {"month": row[0].strftime("%Y-%m"), "total": float(row[1])}
        for row in rows
        if row[0]
    ]


def revenue_payment_status(db: Session, start_dt: datetime, end_dt: datetime) -> dict[str, int]:
    """Payment status distribution."""
    rows = db.execute(
        select(SalesOrder.payment_status, func.count(SalesOrder.id))
        .where(
            SalesOrder.is_active.is_(True),
            SalesOrder.created_at >= start_dt,
            SalesOrder.created_at <= end_dt,
        )
        .group_by(SalesOrder.payment_status)
    ).all()
    return {(s.value if s else "unknown"): c for s, c in rows}


def revenue_order_status(db: Session, start_dt: datetime, end_dt: datetime) -> dict[str, int]:
    """Order status distribution."""
    rows = db.execute(
        select(SalesOrder.status, func.count(SalesOrder.id))
        .where(
            SalesOrder.is_active.is_(True),
            SalesOrder.created_at >= start_dt,
            SalesOrder.created_at <= end_dt,
        )
        .group_by(SalesOrder.status)
    ).all()
    return {(s.value if s else "unknown"): c for s, c in rows}


def revenue_top_subscribers(
    db: Session, start_dt: datetime, end_dt: datetime, limit: int = 20
) -> list[dict]:
    """Top subscribers by revenue."""
    rows = db.execute(
        select(
            SalesOrder.person_id,
            func.sum(SalesOrder.total).label("total_revenue"),
            func.count(SalesOrder.id).label("order_count"),
            func.avg(SalesOrder.total).label("avg_value"),
            func.max(SalesOrder.created_at).label("latest_order"),
        )
        .where(
            SalesOrder.is_active.is_(True),
            SalesOrder.created_at >= start_dt,
            SalesOrder.created_at <= end_dt,
        )
        .group_by(SalesOrder.person_id)
        .order_by(func.sum(SalesOrder.total).desc())
        .limit(limit)
    ).all()

    if not rows:
        return []

    person_ids = [r[0] for r in rows]
    people = db.execute(
        select(Person.id, Person.display_name, Person.first_name, Person.last_name, Person.email)
        .where(Person.id.in_(person_ids))
    ).all()
    person_map = {p[0]: p for p in people}

    # Get subscriber status for these people
    sub_statuses = db.execute(
        select(Subscriber.person_id, Subscriber.status)
        .where(Subscriber.is_active.is_(True), Subscriber.person_id.in_(person_ids))
    ).all()
    sub_status_map = {r[0]: r[1].value if r[1] else "unknown" for r in sub_statuses}

    results = []
    for row in rows:
        p = person_map.get(row[0])
        if not p:
            continue
        name = p.display_name or f"{p.first_name or ''} {p.last_name or ''}".strip() or "Unknown"
        results.append({
            "name": name,
            "email": p.email or "",
            "total_revenue": float(row.total_revenue),
            "order_count": row.order_count,
            "avg_value": round(float(row.avg_value), 2),
            "latest_order": row.latest_order.strftime("%Y-%m-%d") if row.latest_order else "",
            "status": sub_status_map.get(row[0], "N/A"),
        })
    return results


def revenue_outstanding_balances(db: Session, limit: int = 30) -> list[dict]:
    """Orders with outstanding balance."""
    now = datetime.now(UTC)
    rows = db.execute(
        select(
            SalesOrder.order_number,
            SalesOrder.total,
            SalesOrder.amount_paid,
            SalesOrder.balance_due,
            SalesOrder.payment_due_date,
            Person.display_name,
            Person.first_name,
            Person.last_name,
        )
        .join(Person, Person.id == SalesOrder.person_id)
        .where(
            SalesOrder.is_active.is_(True),
            SalesOrder.balance_due > 0,
        )
        .order_by(SalesOrder.balance_due.desc())
        .limit(limit)
    ).all()

    results = []
    for row in rows:
        name = row.display_name or f"{row.first_name or ''} {row.last_name or ''}".strip() or "Unknown"
        days_overdue = 0
        if row.payment_due_date:
            delta = now - row.payment_due_date
            days_overdue = max(0, delta.days)
        results.append({
            "order_number": row.order_number or "",
            "customer": name,
            "total": float(row.total),
            "paid": float(row.amount_paid),
            "balance": float(row.balance_due),
            "due_date": row.payment_due_date.strftime("%Y-%m-%d") if row.payment_due_date else "",
            "days_overdue": days_overdue,
        })
    return results
