"""Subscriber report service functions for reports 1-4."""

import re
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, TypedDict

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.models.bandwidth import BandwidthSample
from app.models.crm.enums import LeadStatus
from app.models.crm.sales import Lead
from app.models.event_store import EventStore
from app.models.person import Person
from app.models.projects import Project
from app.models.sales_order import SalesOrder, SalesOrderPaymentStatus, SalesOrderStatus
from app.models.subscriber import Subscriber, SubscriberStatus
from app.models.tickets import Ticket, TicketSlaEvent, TicketStatus
from app.models.workforce import WorkOrder, WorkOrderStatus

# =====================================================================
# Report 1: Subscriber Overview
# =====================================================================


class ConversionBucket(TypedDict):
    label: str
    min: int
    max: int | None
    count: int


def _overview_subscriber_scope(subscriber_ids: list | None):
    if subscriber_ids is None:
        return []
    return [Subscriber.id.in_(subscriber_ids)]


def _overview_ticket_scope(subscriber_ids: list | None):
    if subscriber_ids is None:
        return []
    return [Ticket.subscriber_id.in_(subscriber_ids)]


def overview_filtered_subscriber_ids(
    db: Session,
    status: SubscriberStatus | None = None,
    region: str | None = None,
) -> list | None:
    if status is None and not region:
        return None

    rows = db.execute(
        select(
            Subscriber.id,
            Subscriber.status,
            _regional_breakdown_key().label("region"),
        )
    ).all()

    return [
        row.id
        for row in rows
        if (status is None or row.status == status) and (not region or _normalize_city_name(row.region) == region)
    ]


def overview_kpis(db: Session, start_dt: datetime, end_dt: datetime, subscriber_ids: list | None = None) -> dict:
    """5 KPI cards for subscriber overview."""
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    subscriber_scope = _overview_subscriber_scope(subscriber_ids)
    ticket_scope = _overview_ticket_scope(subscriber_ids)

    active_count = (
        db.scalar(
            select(func.count(Subscriber.id)).where(
                Subscriber.is_active.is_(True),
                Subscriber.status == SubscriberStatus.active,
                *subscriber_scope,
            )
        )
        or 0
    )

    activations = (
        db.scalar(
            select(func.count(Subscriber.id)).where(
                activation_event_at >= start_dt,
                activation_event_at <= end_dt,
                *subscriber_scope,
            )
        )
        or 0
    )
    terminations = (
        db.scalar(
            select(func.count(Subscriber.id)).where(
                Subscriber.terminated_at >= start_dt,
                Subscriber.terminated_at <= end_dt,
                *subscriber_scope,
            )
        )
        or 0
    )
    net_growth = activations - terminations

    suspended_count = (
        db.scalar(
            select(func.count(Subscriber.id)).where(
                Subscriber.is_active.is_(True),
                Subscriber.status == SubscriberStatus.suspended,
                *subscriber_scope,
            )
        )
        or 0
    )
    total_subs = (
        db.scalar(select(func.count(Subscriber.id)).where(Subscriber.is_active.is_(True), *subscriber_scope)) or 0
    )
    suspended_pct = round(suspended_count / total_subs * 100, 1) if total_subs > 0 else 0

    ticket_count = (
        db.scalar(
            select(func.count(Ticket.id))
            .join(Subscriber, Subscriber.id == Ticket.subscriber_id)
            .where(
                Ticket.is_active.is_(True),
                Ticket.subscriber_id.isnot(None),
                Ticket.created_at >= start_dt,
                Ticket.created_at <= end_dt,
                *ticket_scope,
            )
        )
        or 0
    )
    avg_tickets = round(ticket_count / active_count, 2) if active_count > 0 else 0

    raw_regions = db.scalars(
        select(_regional_breakdown_key()).where(Subscriber.is_active.is_(True), *subscriber_scope)
    ).all()
    region_count = len(
        {_normalize_city_name(raw_region) for raw_region in raw_regions if _normalize_city_name(raw_region)}
    )

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
    db: Session, start_dt: datetime, end_dt: datetime, subscriber_ids: list | None = None
) -> list[dict]:
    """Daily activations vs terminations."""
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    subscriber_scope = _overview_subscriber_scope(subscriber_ids)
    dialect_name = db.get_bind().dialect.name if db.get_bind() is not None else ""

    def _day_bucket(column):
        if dialect_name == "sqlite":
            return func.date(column)
        return func.to_char(func.date_trunc("day", column), "YYYY-MM-DD")

    day_bucket_activation = _day_bucket(activation_event_at).label("day")
    day_bucket_termination = _day_bucket(Subscriber.terminated_at).label("day")

    act_rows = db.execute(
        select(
            day_bucket_activation,
            func.count(Subscriber.id),
        )
        .where(
            activation_event_at >= start_dt,
            activation_event_at <= end_dt,
            *subscriber_scope,
        )
        .group_by("day")
        .order_by("day")
    ).all()

    term_rows = db.execute(
        select(
            day_bucket_termination,
            func.count(Subscriber.id),
        )
        .where(
            Subscriber.terminated_at >= start_dt,
            Subscriber.terminated_at <= end_dt,
            *subscriber_scope,
        )
        .group_by("day")
        .order_by("day")
    ).all()

    def _normalize_day(value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return value[:10]
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d")
        return str(value)[:10]

    act_map = {_normalize_day(row[0]): row[1] for row in act_rows if _normalize_day(row[0])}
    term_map = {_normalize_day(row[0]): row[1] for row in term_rows if _normalize_day(row[0])}
    current_day = start_dt.date()
    end_day = end_dt.date()
    all_days: list[str] = []
    while current_day <= end_day:
        all_days.append(current_day.strftime("%Y-%m-%d"))
        current_day += timedelta(days=1)

    return [
        {
            "date": day,
            "activations": act_map.get(day, 0),
            "terminations": term_map.get(day, 0),
        }
        for day in all_days
    ]


def overview_status_distribution(db: Session, subscriber_ids: list | None = None) -> dict[str, int]:
    """Subscriber counts by status."""
    subscriber_scope = _overview_subscriber_scope(subscriber_ids)
    rows = db.execute(
        select(Subscriber.status, func.count(Subscriber.id))
        .where(Subscriber.is_active.is_(True), *subscriber_scope)
        .group_by(Subscriber.status)
    ).all()
    return {(status.value if status else "unknown"): count for status, count in rows}


def overview_plan_distribution(db: Session, limit: int = 10, subscriber_ids: list | None = None) -> list[dict]:
    """Top service plans by subscriber count."""
    subscriber_scope = _overview_subscriber_scope(subscriber_ids)
    rows = db.execute(
        select(Subscriber.service_plan, func.count(Subscriber.id).label("cnt"))
        .where(
            Subscriber.is_active.is_(True),
            Subscriber.status == SubscriberStatus.active,
            Subscriber.service_plan.isnot(None),
            *subscriber_scope,
        )
        .group_by(Subscriber.service_plan)
        .order_by(func.count(Subscriber.id).desc())
        .limit(limit)
    ).all()
    return [{"plan": row[0], "count": row[1]} for row in rows]


def _regional_breakdown_key():
    return func.coalesce(
        func.nullif(func.trim(Subscriber.service_region), ""),
        func.nullif(func.trim(Subscriber.service_city), ""),
        "Unknown",
    )


def _normalize_region_name(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "Unknown"

    compact = re.sub(r"[\s,.\-_/]+", " ", raw.casefold()).strip()
    canonical = re.sub(r"[^a-z0-9]+", "", raw.casefold())
    if compact in {"unknown", "n a", "na", "none", "null"}:
        return "Unknown"

    if canonical in {
        "abuja",
        "abujafct",
        "fct",
        "fctabuja",
        "federalcapitalterritory",
        "federalcapitalterritoryabuja",
    }:
        return "FCT Abuja"

    if canonical in {
        "lagos",
        "lagoscity",
    }:
        return "Lagos"

    return re.sub(r"\s+", " ", raw).strip().title()


def _normalize_city_name(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "Unknown"

    compact = re.sub(r"[\s,.\-_/]+", " ", raw.casefold()).strip()
    canonical = re.sub(r"[^a-z0-9]+", "", raw.casefold())
    if compact in {"unknown", "n a", "na", "none", "null"}:
        return "Unknown"
    if re.fullmatch(r"\d+", canonical):
        return "Unknown"
    if re.fullmatch(r"-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?", raw.strip()):
        return "Unknown"

    state_patterns = (
        (
            "Abuja",
            (
                "abuja",
                "fct",
                "federal capital territory",
                "wuse",
                "wuze",
                "garki",
                "gariki",
                "karu",
                "gwarimpa",
                "gwarinpa",
                "maitama",
                "maitamma",
                "utako",
                "jabi",
                "gudu",
                "asokoro",
                "lokogoma",
                "katampe",
                "lugbe",
                "mpape",
                "guzape",
                "life camp",
                "lifecamp",
                "kubwa",
            ),
        ),
        (
            "Lagos",
            (
                "lagos",
                "ikeja",
                "lekki",
                "ajah",
                "ikoyi",
                "victoria island",
                "vi",
                "surulere",
                "yaba",
                "maryland",
                "magodo",
                "ogudu",
                "ikorodu",
                "festac",
                "ajah",
                "epe",
            ),
        ),
        (
            "Rivers",
            (
                "port harcourt",
                "ph",
                "trans amadi",
                "rumuola",
                "rumuokoro",
                "rupokwu",
                "rumuokwurusi",
                "gra phase",
                "old gra",
            ),
        ),
        (
            "Oyo",
            (
                "ibadan",
                "bodija",
                "challenge",
                "apata",
                "moniya",
                "ui",
                "ojoo",
                "ring road",
            ),
        ),
        (
            "Kano",
            (
                "kano",
                "nassarawa",
                "nasarawa kano",
                "sabongari",
                "sabon gari",
                "hotoro",
                "dala",
            ),
        ),
        (
            "Enugu",
            (
                "enugu",
                "independence layout",
                "new haven",
                "trans ekulu",
                "g ra enugu",
                "gra enugu",
            ),
        ),
        (
            "Kaduna",
            (
                "kaduna",
                "barnawa",
                "malali",
                "sabon tasha",
                "kawo",
                "gonin gora",
            ),
        ),
        (
            "Ogun",
            (
                "abeokuta",
                "ijaye",
                "adigbe",
                "panseke",
                "onikolobo",
            ),
        ),
        (
            "Edo",
            (
                "benin",
                "benin city",
                "sapele road",
                "u selu",
                "use lu",
                "g ra benin",
                "gra benin",
            ),
        ),
        (
            "Plateau",
            (
                "jos",
                "rayfield",
                "bukuru",
                "hwolshe",
                "vom",
            ),
        ),
        (
            "Adamawa",
            (
                "adamawa",
                "yola",
                "jimeta",
                "mubi",
            ),
        ),
        (
            "Kogi",
            (
                "kogi",
                "lokoja",
                "anyigba",
                "okene",
            ),
        ),
        (
            "Anambra",
            (
                "anambra",
                "awka",
                "nnewi",
                "onitsha",
            ),
        ),
        (
            "Osun",
            (
                "osun",
                "oshogbo",
                "osogbo",
                "ife",
                "ilesa",
            ),
        ),
        (
            "Ondo",
            (
                "ondo",
                "akure",
                "ondo town",
            ),
        ),
        (
            "Imo",
            (
                "imo",
                "owerri",
                "orlu",
            ),
        ),
        (
            "Yobe",
            (
                "yobe",
                "damaturu",
                "potiskum",
            ),
        ),
    )

    for state, needles in state_patterns:
        if canonical.startswith("fct") and state == "Abuja":
            return state
        if any(needle in compact for needle in needles):
            return state

    # Extract final state token from strings like "Lokoja, Kogi"
    state_tokens = {
        "abia": "Abia",
        "adamawa": "Adamawa",
        "akwa ibom": "Akwa Ibom",
        "anambra": "Anambra",
        "bauchi": "Bauchi",
        "bayelsa": "Bayelsa",
        "benue": "Benue",
        "borno": "Borno",
        "cross river": "Cross River",
        "delta": "Delta",
        "ebonyi": "Ebonyi",
        "edo": "Edo",
        "ekiti": "Ekiti",
        "enugu": "Enugu",
        "gombe": "Gombe",
        "imo": "Imo",
        "jigawa": "Jigawa",
        "kaduna": "Kaduna",
        "kano": "Kano",
        "katsina": "Katsina",
        "kebbi": "Kebbi",
        "kogi": "Kogi",
        "kwara": "Kwara",
        "lagos": "Lagos",
        "nasarawa": "Nasarawa",
        "nassarawa": "Nasarawa",
        "niger": "Niger",
        "ogun": "Ogun",
        "ondo": "Ondo",
        "osun": "Osun",
        "oyo": "Oyo",
        "plateau": "Plateau",
        "rivers": "Rivers",
        "sokoto": "Sokoto",
        "taraba": "Taraba",
        "yobe": "Yobe",
        "zamfara": "Zamfara",
        "abuja": "Abuja",
        "fct": "Abuja",
    }
    parts = [p.strip() for p in re.split(r"[,;/|-]+", compact) if p.strip()]
    for part in reversed(parts):
        if part in state_tokens:
            return state_tokens[part]

    return "Unknown"


def _clean_report_name(value: str | None) -> str:
    raw = re.sub(r"\s+", " ", (value or "")).strip()
    if not raw:
        return "Unknown"
    if raw.isupper() and len(raw) <= 4:
        return raw
    if re.search(r"\d", raw) and "-" in raw and raw.upper() == raw:
        return raw

    words = []
    for word in raw.split(" "):
        if word.isupper() and len(word) <= 4:
            words.append(word)
        else:
            words.append(word.title())

    cleaned = " ".join(words)
    cleaned = re.sub(r"'S\b", "'s", cleaned)
    return cleaned


def overview_regional_breakdown(
    db: Session,
    start_dt: datetime,
    end_dt: datetime,
    subscriber_ids: list | None = None,
) -> list[dict]:
    """Regional breakdown table."""
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    raw_region_key = _regional_breakdown_key().label("region")
    subscriber_scope = _overview_subscriber_scope(subscriber_ids)
    ticket_scope = _overview_ticket_scope(subscriber_ids)

    regions = db.execute(
        select(
            raw_region_key,
            func.count(Subscriber.id).filter(Subscriber.status == SubscriberStatus.active).label("active"),
            func.count(Subscriber.id).filter(Subscriber.status == SubscriberStatus.suspended).label("suspended"),
            func.count(Subscriber.id).filter(Subscriber.status == SubscriberStatus.terminated).label("terminated"),
            func.count(Subscriber.id)
            .filter(
                activation_event_at >= start_dt,
                activation_event_at <= end_dt,
            )
            .label("new_in_period"),
        )
        .where(Subscriber.is_active.is_(True), *subscriber_scope)
        .group_by(raw_region_key)
        .order_by(func.count(Subscriber.id).desc())
    ).all()

    # Ticket counts per region
    subscriber_regions = (
        select(
            Subscriber.id.label("subscriber_id"),
            _regional_breakdown_key().label("region"),
        )
        .where(Subscriber.is_active.is_(True), *subscriber_scope)
        .subquery()
    )
    ticket_rows = (
        db.execute(
            select(subscriber_regions.c.region, func.count(Ticket.id))
            .join(Ticket, Ticket.subscriber_id == subscriber_regions.c.subscriber_id)
            .where(
                Ticket.is_active.is_(True),
                Ticket.created_at >= start_dt,
                Ticket.created_at <= end_dt,
                *ticket_scope,
            )
            .group_by(subscriber_regions.c.region)
        ).all()
        if regions
        else []
    )
    aggregated: dict[str, dict] = {}

    for row in regions:
        normalized_region = _normalize_city_name(row[0])
        bucket = aggregated.setdefault(
            normalized_region,
            {
                "region": normalized_region,
                "active": 0,
                "suspended": 0,
                "terminated": 0,
                "new_in_period": 0,
                "ticket_count": 0,
            },
        )
        bucket["active"] += row[1]
        bucket["suspended"] += row[2]
        bucket["terminated"] += row[3]
        bucket["new_in_period"] += row[4]

    for raw_region, ticket_count in ticket_rows:
        normalized_region = _normalize_city_name(raw_region)
        bucket = aggregated.setdefault(
            normalized_region,
            {
                "region": normalized_region,
                "active": 0,
                "suspended": 0,
                "terminated": 0,
                "new_in_period": 0,
                "ticket_count": 0,
            },
        )
        bucket["ticket_count"] += ticket_count

    return sorted(aggregated.values(), key=lambda row: (-row["active"], -row["new_in_period"], row["region"]))


def overview_filter_options(db: Session) -> dict:
    """Dropdown options for overview filters."""
    raw_region_key = _regional_breakdown_key().label("region")
    region_rows = select(raw_region_key).where(Subscriber.is_active.is_(True)).distinct().subquery()
    raw_regions = db.scalars(select(region_rows.c.region).order_by(region_rows.c.region)).all()
    regions = sorted(
        {_normalize_city_name(raw_region) for raw_region in raw_regions if _normalize_city_name(raw_region)}
    )
    plans = db.scalars(
        select(func.distinct(Subscriber.service_plan))
        .where(Subscriber.is_active.is_(True), Subscriber.service_plan.isnot(None))
        .order_by(Subscriber.service_plan)
    ).all()
    return {"regions": regions, "plans": plans}


# =====================================================================
# Report 2: Subscriber Lifecycle
# =====================================================================


def lifecycle_kpis(db: Session, start_dt: datetime, end_dt: datetime) -> dict:
    """5 KPI cards for lifecycle report."""
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    churn_event_at = _churn_event_at()

    # Leads created in period
    leads_created = (
        db.scalar(
            select(func.count(Lead.id)).where(
                Lead.is_active.is_(True),
                Lead.created_at >= start_dt,
                Lead.created_at <= end_dt,
            )
        )
        or 0
    )
    leads_won = (
        db.scalar(
            select(func.count(Lead.id)).where(
                Lead.is_active.is_(True),
                Lead.status == LeadStatus.won,
                Lead.closed_at >= start_dt,
                Lead.closed_at <= end_dt,
            )
        )
        or 0
    )
    conversion_rate = round(leads_won / leads_created * 100, 1) if leads_created > 0 else 0

    # Avg days to convert based on lead cycle time for won deals in period.
    dialect_name = db.get_bind().dialect.name if db.get_bind() is not None else ""
    if dialect_name == "sqlite":
        avg_days_expr = func.julianday(Lead.closed_at) - func.julianday(Lead.created_at)
    else:
        avg_days_expr = func.extract("epoch", Lead.closed_at - Lead.created_at) / 86400

    avg_days_result = db.scalar(
        select(func.avg(avg_days_expr)).where(
            Lead.is_active.is_(True),
            Lead.status == LeadStatus.won,
            Lead.created_at.isnot(None),
            Lead.closed_at.isnot(None),
            Lead.closed_at >= start_dt,
            Lead.closed_at <= end_dt,
        )
    )
    avg_days_to_convert = round(float(avg_days_result), 1) if avg_days_result else 0

    # Churn rate
    active_at_start = (
        db.scalar(
            select(func.count(Subscriber.id)).where(
                activation_event_at < start_dt,
                ((churn_event_at.is_(None)) | (churn_event_at >= start_dt)),
            )
        )
        or 0
    )
    terminated_in_period = (
        db.scalar(
            select(func.count(Subscriber.id)).where(
                churn_event_at >= start_dt,
                churn_event_at <= end_dt,
            )
        )
        or 0
    )
    churn_rate = (terminated_in_period / active_at_start * 100) if active_at_start > 0 else 0

    churn_rows = _lifecycle_churn_rows(db, start_dt, end_dt, strict_terminated_only=True)
    avg_lifecycle_days = round(sum(row["tenure_days"] for row in churn_rows) / len(churn_rows), 1) if churn_rows else 0
    avg_lifecycle_months = round(avg_lifecycle_days / 30.4, 1) if avg_lifecycle_days > 0 else 0

    upgraded_in_period = (
        db.scalar(
            select(func.count(EventStore.id)).where(
                EventStore.is_active.is_(True),
                EventStore.event_type == "subscription.upgraded",
                EventStore.created_at >= start_dt,
                EventStore.created_at <= end_dt,
            )
        )
        or 0
    )
    downgraded_in_period = (
        db.scalar(
            select(func.count(EventStore.id)).where(
                EventStore.is_active.is_(True),
                EventStore.event_type == "subscription.downgraded",
                EventStore.created_at >= start_dt,
                EventStore.created_at <= end_dt,
            )
        )
        or 0
    )
    upgrade_rate = round(upgraded_in_period / active_at_start * 100, 1) if active_at_start > 0 else 0
    downgrade_rate = round(downgraded_in_period / active_at_start * 100, 1) if active_at_start > 0 else 0

    # Engagement is a weighted subscriber-level activity score normalized to 0-100.
    # Presence of each activity type contributes once per subscriber in the period.
    active_subscriber_ids = db.scalars(
        select(Subscriber.id).where(
            activation_event_at <= end_dt,
            ((churn_event_at.is_(None)) | (churn_event_at > end_dt)),
        )
    ).all()
    active_subscriber_id_set = {str(subscriber_id) for subscriber_id in active_subscriber_ids if subscriber_id}

    ticket_subscriber_ids = db.scalars(
        select(func.distinct(Ticket.subscriber_id)).where(
            Ticket.is_active.is_(True),
            Ticket.subscriber_id.isnot(None),
            Ticket.created_at >= start_dt,
            Ticket.created_at <= end_dt,
        )
    ).all()
    work_order_subscriber_ids = db.scalars(
        select(func.distinct(WorkOrder.subscriber_id)).where(
            WorkOrder.is_active.is_(True),
            WorkOrder.subscriber_id.isnot(None),
            WorkOrder.created_at >= start_dt,
            WorkOrder.created_at <= end_dt,
        )
    ).all()
    event_subscriber_ids = db.scalars(
        select(func.distinct(EventStore.subscriber_id)).where(
            EventStore.is_active.is_(True),
            EventStore.subscriber_id.isnot(None),
            EventStore.created_at >= start_dt,
            EventStore.created_at <= end_dt,
        )
    ).all()
    ticket_active_ids = {
        str(subscriber_id) for subscriber_id in ticket_subscriber_ids if subscriber_id
    } & active_subscriber_id_set
    work_order_active_ids = {
        str(subscriber_id) for subscriber_id in work_order_subscriber_ids if subscriber_id
    } & active_subscriber_id_set
    event_active_ids = {
        str(subscriber_id) for subscriber_id in event_subscriber_ids if subscriber_id
    } & active_subscriber_id_set
    max_weight_per_subscriber = 3.0
    weighted_activity_total = (
        len(ticket_active_ids) * 1.0 + len(work_order_active_ids) * 1.5 + len(event_active_ids) * 0.5
    )
    engagement_score = (
        round(weighted_activity_total / (len(active_subscriber_id_set) * max_weight_per_subscriber) * 100, 1)
        if active_subscriber_id_set
        else 0
    )

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

    total_billed_selected_range = db.scalar(
        select(func.coalesce(func.sum(SalesOrder.total), 0))
        .join(Subscriber, Subscriber.person_id == SalesOrder.person_id)
        .where(
            SalesOrder.is_active.is_(True),
            SalesOrder.status.in_([SalesOrderStatus.confirmed, SalesOrderStatus.paid, SalesOrderStatus.fulfilled]),
            SalesOrder.created_at >= start_dt,
            SalesOrder.created_at <= end_dt,
            Subscriber.is_active.is_(True),
            Subscriber.status == SubscriberStatus.active,
        )
    ) or Decimal("0")

    return {
        "conversion_rate": conversion_rate,
        "avg_days_to_convert": avg_days_to_convert,
        "churn_rate": churn_rate,
        "terminated_in_period": terminated_in_period,
        "avg_lifecycle_days": avg_lifecycle_days,
        "avg_lifecycle_months": avg_lifecycle_months,
        "upgraded_in_period": upgraded_in_period,
        "downgraded_in_period": downgraded_in_period,
        "upgrade_rate": upgrade_rate,
        "downgrade_rate": downgrade_rate,
        "engagement_score": engagement_score,
        "pipeline_value": float(pipeline_value),
        "total_billed_selected_range": float(total_billed_selected_range),
        "leads_won": leads_won,
    }


def lifecycle_funnel(db: Session) -> list[dict]:
    """Person counts by party_status sorted by descending stage size."""
    rows = db.execute(
        select(Person.party_status, func.count(Person.id))
        .where(Person.is_active.is_(True))
        .group_by(Person.party_status)
    ).all()
    status_map = {(s.value if s else "unknown"): c for s, c in rows}
    order = ["lead", "contact", "customer", "subscriber"]
    funnel = [{"stage": stage, "count": status_map.get(stage, 0)} for stage in order]
    return sorted(funnel, key=lambda item: (-item["count"], order.index(item["stage"])))


def lifecycle_churn_trend(db: Session) -> list[dict]:
    """Monthly churn count over the last 12 months."""
    current_month = _month_start(datetime.now(UTC))
    cutoff = _add_months(current_month, -11)
    churn_event_at = _churn_event_at()
    dialect_name = db.get_bind().dialect.name if db.get_bind() is not None else ""
    if dialect_name == "sqlite":
        churn_month = func.strftime("%Y-%m", churn_event_at).label("month")
    else:
        churn_month = func.to_char(func.date_trunc("month", churn_event_at), "YYYY-MM").label("month")

    rows = db.execute(
        select(
            churn_month,
            func.count(Subscriber.id).label("total_churn"),
        )
        .where(
            churn_event_at.isnot(None),
            churn_event_at >= cutoff,
        )
        .group_by("month")
        .order_by("month")
    ).all()
    counts_by_month = {str(row.month)[:7]: int(row.total_churn or 0) for row in rows if row.month}

    trend: list[dict] = []
    month_cursor = cutoff
    while month_cursor <= current_month:
        month_key = month_cursor.strftime("%Y-%m")
        trend.append(
            {
                "month": month_cursor.strftime("%b %Y"),
                "month_key": month_key,
                "count": counts_by_month.get(month_key, 0),
            }
        )
        month_cursor = _add_months(month_cursor, 1)
    return trend


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
    return [{"source": row[0], "total": row[1], "won": row[2]} for row in rows]


def lifecycle_retention_cohorts(db: Session, start_dt: datetime, end_dt: datetime) -> dict[str, list[dict] | list[str]]:
    """Monthly activation cohorts with retention percentages through the selected range."""
    max_months = 12
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    churn_event_at = _churn_event_at()
    start_month = _month_start(start_dt)
    end_month = _month_start(end_dt)
    month_span = (end_month.year - start_month.year) * 12 + (end_month.month - start_month.month) + 1
    if month_span > max_months:
        start_month = _add_months(end_month, -(max_months - 1))

    rows = db.execute(
        select(
            activation_event_at.label("activation_event_at"),
            churn_event_at.label("churn_event_at"),
        ).where(
            activation_event_at.isnot(None),
            activation_event_at >= start_month,
            activation_event_at <= end_dt,
        )
    ).all()

    month_labels: list[str] = []
    month_cursor = start_month
    while month_cursor <= end_month:
        month_labels.append(month_cursor.strftime("%Y-%m"))
        month_cursor = _add_months(month_cursor, 1)

    cohorts: dict[str, list[dict[str, datetime | None]]] = defaultdict(list)
    for row in rows:
        activation_at = row.activation_event_at
        if activation_at is None:
            continue
        cohort_key = _month_start(activation_at).strftime("%Y-%m")
        cohorts[cohort_key].append(
            {
                "activation_event_at": activation_at,
                "churn_event_at": row.churn_event_at,
            }
        )

    cohort_rows: list[dict] = []
    for cohort_key in month_labels:
        members = cohorts.get(cohort_key, [])
        if not members:
            continue
        cohort_month = datetime.strptime(cohort_key, "%Y-%m").replace(tzinfo=UTC)
        values: list[dict[str, float | int | str]] = []
        for month_index, month_label in enumerate(month_labels):
            target_month = datetime.strptime(month_label, "%Y-%m").replace(tzinfo=UTC)
            if target_month < cohort_month:
                values.append({"label": month_label, "retention_pct": 0, "retained": 0})
                continue

            snapshot_end = _month_end(target_month)
            retained = sum(
                1
                for member in members
                if member["churn_event_at"] is None or _ensure_utc(member["churn_event_at"]) > snapshot_end
            )
            retention_pct = round(retained / len(members) * 100, 1) if members else 0
            values.append(
                {
                    "label": month_label,
                    "retention_pct": retention_pct,
                    "retained": retained,
                    "month_index": month_index,
                }
            )

        cohort_rows.append(
            {
                "cohort": cohort_key,
                "size": len(members),
                "values": values,
            }
        )

    return {"months": month_labels, "rows": cohort_rows}


def lifecycle_time_to_convert_distribution(db: Session, start_dt: datetime, end_dt: datetime) -> list[dict]:
    """Histogram buckets for won lead conversion cycle time."""
    dialect_name = db.get_bind().dialect.name if db.get_bind() is not None else ""
    if dialect_name == "sqlite":
        days_expr = func.julianday(Lead.closed_at) - func.julianday(Lead.created_at)
    else:
        days_expr = func.extract("epoch", Lead.closed_at - Lead.created_at) / 86400

    rows = db.execute(
        select(days_expr.label("days_to_convert")).where(
            Lead.is_active.is_(True),
            Lead.status == LeadStatus.won,
            Lead.created_at.isnot(None),
            Lead.closed_at.isnot(None),
            Lead.closed_at >= start_dt,
            Lead.closed_at <= end_dt,
        )
    ).all()

    buckets: list[ConversionBucket] = [
        {"label": "0-7 days", "min": 0, "max": 7, "count": 0},
        {"label": "8-14 days", "min": 8, "max": 14, "count": 0},
        {"label": "15-30 days", "min": 15, "max": 30, "count": 0},
        {"label": "31-60 days", "min": 31, "max": 60, "count": 0},
        {"label": "61-90 days", "min": 61, "max": 90, "count": 0},
        {"label": "91+ days", "min": 91, "max": None, "count": 0},
    ]

    for row in rows:
        days_to_convert = int(max(0, round(float(row.days_to_convert or 0))))
        for bucket in buckets:
            upper_bound = bucket["max"]
            if days_to_convert >= bucket["min"] and (upper_bound is None or days_to_convert <= upper_bound):
                bucket["count"] += 1
                break

    return [{"label": bucket["label"], "count": bucket["count"]} for bucket in buckets]


def lifecycle_plan_migration_flow(db: Session, start_dt: datetime, end_dt: datetime, limit: int = 10) -> list[dict]:
    """Aggregate plan-to-plan subscription migration events for sankey-style rendering."""
    rows = db.execute(
        select(
            EventStore.event_type,
            EventStore.payload,
        ).where(
            EventStore.is_active.is_(True),
            EventStore.event_type.in_(["subscription.upgraded", "subscription.downgraded"]),
            EventStore.created_at >= start_dt,
            EventStore.created_at <= end_dt,
        )
    ).all()

    flow_counts: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        source_plan = _extract_plan_name(
            payload,
            ["from_plan", "old_plan", "previous_plan", "source_plan", "old_service_plan", "previous_service_plan"],
        )
        target_plan = _extract_plan_name(
            payload,
            ["to_plan", "new_plan", "current_plan", "target_plan", "new_service_plan", "service_plan"],
        )

        if not source_plan or not target_plan or source_plan == target_plan:
            before = payload.get("before") if isinstance(payload.get("before"), dict) else {}
            after = payload.get("after") if isinstance(payload.get("after"), dict) else {}
            source_plan = source_plan or _extract_plan_name(before, ["service_plan", "plan"])
            target_plan = target_plan or _extract_plan_name(after, ["service_plan", "plan"])

        if not source_plan or not target_plan or source_plan == target_plan:
            continue

        flow_counts[(source_plan, target_plan)] += 1

    flows = [
        {"source": source, "target": target, "count": count}
        for (source, target), count in sorted(flow_counts.items(), key=lambda item: (-item[1], item[0][0], item[0][1]))[
            :limit
        ]
    ]
    return flows


def lifecycle_recent_churns(db: Session, limit: int = 5) -> list[dict]:
    """Recently churned subscribers in the last 30 days."""
    cutoff = datetime.now(UTC) - timedelta(days=30)
    return _lifecycle_churn_rows(db, cutoff, datetime.now(UTC), limit=limit)


def _lifecycle_churn_rows(
    db: Session,
    start_dt: datetime,
    end_dt: datetime,
    limit: int | None = None,
    strict_terminated_only: bool = False,
) -> list[dict]:
    """Build churn rows for the selected date range."""
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    churn_event_at = _strict_churn_event_at() if strict_terminated_only else _churn_event_at()
    query = (
        select(
            Subscriber.subscriber_number,
            Subscriber.service_plan,
            Subscriber.service_region,
            activation_event_at.label("activation_event_at"),
            churn_event_at.label("churn_event_at"),
            Person.first_name,
            Person.last_name,
            Person.display_name,
        )
        .outerjoin(Person, Person.id == Subscriber.person_id)
        .where(
            churn_event_at.isnot(None),
            churn_event_at >= start_dt,
            churn_event_at <= end_dt,
        )
        .order_by(churn_event_at.desc())
    )
    if limit is not None:
        query = query.limit(limit)
    subs = db.execute(query).all()

    results = []
    for row in subs:
        name = row.display_name or f"{row.first_name or ''} {row.last_name or ''}".strip() or "Unknown"
        tenure = 0
        if row.activation_event_at and row.churn_event_at:
            tenure = max(0, (row.churn_event_at.date() - row.activation_event_at.date()).days)
        results.append(
            {
                "name": name,
                "subscriber_number": row.subscriber_number or "",
                "plan": row.service_plan or "",
                "region": row.service_region or "",
                "activated_at": row.activation_event_at.strftime("%Y-%m-%d") if row.activation_event_at else "",
                "terminated_at": row.churn_event_at.strftime("%Y-%m-%d") if row.churn_event_at else "",
                "tenure_days": tenure,
            }
        )
    return results


def _month_start(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_months(value: datetime, months: int) -> datetime:
    month_index = (value.year * 12 + (value.month - 1)) + months
    year = month_index // 12
    month = month_index % 12 + 1
    return value.replace(year=year, month=month, day=1)


def _month_end(value: datetime) -> datetime:
    next_month = _add_months(_month_start(value), 1)
    return next_month - timedelta(seconds=1)


def _extract_plan_name(payload: Mapping[str, Any] | None, keys: list[str]) -> str | None:
    if not payload:
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def churned_subscribers_kpis(db: Session, start_dt: datetime, end_dt: datetime) -> dict:
    """Summary metrics for churned subscribers in a date range."""
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    churn_event_at = _strict_churn_event_at()
    churned_rows = db.execute(
        select(
            Subscriber.person_id,
            Subscriber.service_plan,
            Subscriber.service_speed,
            Subscriber.service_region,
            activation_event_at.label("activation_event_at"),
            churn_event_at.label("churn_event_at"),
            Person.first_name,
            Person.last_name,
            Person.display_name,
        )
        .outerjoin(Person, Person.id == Subscriber.person_id)
        .where(
            churn_event_at.isnot(None),
            churn_event_at >= start_dt,
            churn_event_at <= end_dt,
        )
    ).all()

    churned_count = len(churned_rows)
    tenure_days: list[int] = []
    plan_counts: dict[str, int] = defaultdict(int)
    impacted_regions: set[str] = set()
    revenue_lost_to_churn = 0.0
    churned_people: dict[Any, str] = {}
    fallback_names: list[str] = []

    for row in churned_rows:
        if row.activation_event_at and row.churn_event_at:
            tenure_days.append(max(0, (row.churn_event_at.date() - row.activation_event_at.date()).days))
        plan_name = (row.service_plan or "").strip() or "Unknown"
        plan_counts[plan_name] += 1
        revenue_lost_to_churn += _estimate_monthly_plan_value(row.service_plan, row.service_speed)
        region_name = (
            _normalize_city_name(getattr(row, "service_region", None)) if hasattr(row, "service_region") else ""
        )
        if region_name:
            impacted_regions.add(region_name)

        raw_name = row.display_name or f"{row.first_name or ''} {row.last_name or ''}".strip()
        clean_name = _clean_report_name(raw_name) if raw_name else ""
        if row.person_id and clean_name and row.person_id not in churned_people:
            churned_people[row.person_id] = clean_name
        elif clean_name:
            fallback_names.append(clean_name)

    avg_tenure_days = round(sum(tenure_days) / len(tenure_days), 1) if tenure_days else 0
    avg_lifetime_months = round(avg_tenure_days / 30.4, 1) if avg_tenure_days > 0 else 0

    active_at_start = (
        db.scalar(
            select(func.count(Subscriber.id)).where(
                activation_event_at < start_dt,
                ((churn_event_at.is_(None)) | (churn_event_at >= start_dt)),
            )
        )
        or 0
    )
    churn_rate = round(churned_count / active_at_start * 100, 1) if active_at_start > 0 else 0

    top_churn_plan_type = "N/A"
    top_churn_plan_count = 0
    if plan_counts:
        top_churn_plan_type, top_churn_plan_count = sorted(
            plan_counts.items(),
            key=lambda item: (-item[1], item[0].lower()),
        )[0]

    high_value_customer_lost_name = "N/A"
    high_value_customer_lost_paid = 0.0
    if churned_people:
        top_value_row = db.execute(
            select(
                SalesOrder.person_id,
                func.coalesce(func.sum(SalesOrder.amount_paid), 0).label("total_paid"),
            )
            .where(
                SalesOrder.is_active.is_(True),
                SalesOrder.person_id.in_(list(churned_people.keys())),
                SalesOrder.status.in_([SalesOrderStatus.confirmed, SalesOrderStatus.paid, SalesOrderStatus.fulfilled]),
            )
            .group_by(SalesOrder.person_id)
            .order_by(func.coalesce(func.sum(SalesOrder.amount_paid), 0).desc(), SalesOrder.person_id)
            .limit(1)
        ).first()
        if top_value_row:
            high_value_customer_lost_name = churned_people.get(top_value_row.person_id, "N/A")
            high_value_customer_lost_paid = round(float(top_value_row.total_paid or 0), 2)
        elif fallback_names:
            high_value_customer_lost_name = sorted(fallback_names)[0]

    return {
        "churned_count": churned_count,
        "churn_rate": churn_rate,
        "revenue_lost_to_churn": round(revenue_lost_to_churn, 2),
        "top_churn_plan_type": top_churn_plan_type,
        "top_churn_plan_count": top_churn_plan_count,
        "avg_lifetime_before_churn_days": avg_tenure_days,
        "avg_lifetime_before_churn_months": avg_lifetime_months,
        "high_value_customer_lost_name": high_value_customer_lost_name,
        "high_value_customer_lost_paid": high_value_customer_lost_paid,
        "avg_tenure_days": avg_tenure_days,
        "impacted_regions": len(impacted_regions),
        "impacted_plans": len(plan_counts),
    }


def churned_subscribers_trend(db: Session, start_dt: datetime, end_dt: datetime) -> list[dict]:
    """Daily churn count in the selected date range."""
    churn_event_at = _strict_churn_event_at()
    dialect_name = db.get_bind().dialect.name if db.get_bind() is not None else ""
    if dialect_name == "sqlite":
        churn_day = func.date(churn_event_at).label("day")
    else:
        churn_day = func.to_char(func.date_trunc("day", churn_event_at), "YYYY-MM-DD").label("day")

    rows = db.execute(
        select(
            churn_day,
            func.count(Subscriber.id).label("count"),
        )
        .where(
            churn_event_at.isnot(None),
            churn_event_at >= start_dt,
            churn_event_at <= end_dt,
        )
        .group_by("day")
        .order_by("day")
    ).all()
    return [{"date": str(row.day)[:10], "count": int(row._mapping["count"] or 0)} for row in rows if row.day]


def churned_subscribers_rows(db: Session, start_dt: datetime, end_dt: datetime, limit: int = 50) -> list[dict]:
    """Detailed churned subscribers in the selected date range."""
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    churn_event_at = _strict_churn_event_at()
    subs = db.execute(
        select(
            Subscriber.subscriber_number,
            Subscriber.service_plan,
            Subscriber.service_region,
            activation_event_at.label("activation_event_at"),
            churn_event_at.label("churn_event_at"),
            Person.first_name,
            Person.last_name,
            Person.display_name,
        )
        .outerjoin(Person, Person.id == Subscriber.person_id)
        .where(
            churn_event_at.isnot(None),
            churn_event_at >= start_dt,
            churn_event_at <= end_dt,
        )
        .order_by(churn_event_at.desc())
        .limit(limit)
    ).all()

    results = []
    for row in subs:
        raw_name = row.display_name or f"{row.first_name or ''} {row.last_name or ''}".strip() or row.subscriber_number
        name = _clean_report_name(raw_name)
        region = _normalize_city_name(row.service_region)
        tenure = 0
        if row.activation_event_at and row.churn_event_at:
            tenure = max(0, (row.churn_event_at.date() - row.activation_event_at.date()).days)
        results.append(
            {
                "name": name,
                "subscriber_number": row.subscriber_number or "",
                "plan": row.service_plan or "",
                "region": region,
                "activated_at": row.activation_event_at.strftime("%Y-%m-%d") if row.activation_event_at else "",
                "terminated_at": row.churn_event_at.strftime("%Y-%m-%d") if row.churn_event_at else "",
                "tenure_days": tenure,
            }
        )
    return results


def churned_failed_payment_rows(db: Session, start_dt: datetime, end_dt: datetime, limit: int = 50) -> list[dict]:
    """Terminated subscribers in period with outstanding failed payment signals."""
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    churn_event_at = _strict_churn_event_at()
    rows = db.execute(
        select(
            Subscriber.subscriber_number,
            Subscriber.service_plan,
            activation_event_at.label("activation_event_at"),
            churn_event_at.label("churn_event_at"),
            Person.first_name,
            Person.last_name,
            Person.display_name,
            func.coalesce(func.sum(SalesOrder.amount_paid), 0).label("total_paid"),
            func.coalesce(func.sum(SalesOrder.balance_due), 0).label("outstanding_balance"),
            func.max(SalesOrder.payment_due_date).label("latest_due_date"),
            func.max(SalesOrder.updated_at).label("latest_payment_update"),
        )
        .outerjoin(Person, Person.id == Subscriber.person_id)
        .join(
            SalesOrder,
            (SalesOrder.person_id == Subscriber.person_id)
            & SalesOrder.is_active.is_(True)
            & SalesOrder.payment_status.in_([SalesOrderPaymentStatus.pending, SalesOrderPaymentStatus.partial])
            & (SalesOrder.balance_due > 0),
        )
        .where(
            churn_event_at.isnot(None),
            churn_event_at >= start_dt,
            churn_event_at <= end_dt,
        )
        .group_by(
            Subscriber.subscriber_number,
            Subscriber.service_plan,
            activation_event_at,
            churn_event_at,
            Person.first_name,
            Person.last_name,
            Person.display_name,
        )
        .order_by(churn_event_at.desc(), func.coalesce(func.sum(SalesOrder.balance_due), 0).desc())
        .limit(limit)
    ).all()

    results = []
    for row in rows:
        raw_name = row.display_name or f"{row.first_name or ''} {row.last_name or ''}".strip() or row.subscriber_number
        name = _clean_report_name(raw_name)
        results.append(
            {
                "name": name,
                "subscriber_number": row.subscriber_number or "",
                "plan": row.service_plan or "",
                "activated_at": row.activation_event_at.strftime("%Y-%m-%d") if row.activation_event_at else "",
                "terminated_at": row.churn_event_at.strftime("%Y-%m-%d") if row.churn_event_at else "",
                "total_paid": round(float(row.total_paid or 0), 2),
                "outstanding_balance": round(float(row.outstanding_balance or 0), 2),
                "due_date": row.latest_due_date.strftime("%Y-%m-%d") if row.latest_due_date else "",
                "payment_updated_at": row.latest_payment_update.strftime("%Y-%m-%d")
                if row.latest_payment_update
                else "",
            }
        )
    return results


def churned_cancelled_rows(db: Session, start_dt: datetime, end_dt: datetime, limit: int = 50) -> list[dict]:
    """Subscribers lost due to explicit cancellation events in the selected period."""
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    rows = db.execute(
        select(
            Subscriber.subscriber_number,
            Subscriber.service_plan,
            Subscriber.service_region,
            activation_event_at.label("activation_event_at"),
            func.max(EventStore.created_at).label("canceled_at"),
            Person.first_name,
            Person.last_name,
            Person.display_name,
        )
        .join(EventStore, EventStore.subscriber_id == Subscriber.id)
        .outerjoin(Person, Person.id == Subscriber.person_id)
        .where(
            EventStore.is_active.is_(True),
            EventStore.event_type == "subscription.canceled",
            EventStore.created_at >= start_dt,
            EventStore.created_at <= end_dt,
        )
        .group_by(
            Subscriber.subscriber_number,
            Subscriber.service_plan,
            Subscriber.service_region,
            activation_event_at,
            Person.first_name,
            Person.last_name,
            Person.display_name,
        )
        .order_by(func.max(EventStore.created_at).desc())
        .limit(limit)
    ).all()

    results = []
    for row in rows:
        raw_name = row.display_name or f"{row.first_name or ''} {row.last_name or ''}".strip() or row.subscriber_number
        name = _clean_report_name(raw_name)
        region = _normalize_city_name(row.service_region)
        tenure = 0
        if row.activation_event_at and row.canceled_at:
            tenure = max(0, (row.canceled_at.date() - row.activation_event_at.date()).days)
        results.append(
            {
                "name": name,
                "subscriber_number": row.subscriber_number or "",
                "plan": row.service_plan or "",
                "region": region,
                "activated_at": row.activation_event_at.strftime("%Y-%m-%d") if row.activation_event_at else "",
                "terminated_at": row.canceled_at.strftime("%Y-%m-%d") if row.canceled_at else "",
                "tenure_days": tenure,
            }
        )
    return results


def churned_inactive_usage_rows(db: Session, end_dt: datetime, limit: int = 50) -> list[dict]:
    """Subscribers with no recorded bandwidth usage in the last 90 days."""
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    usage_cutoff = end_dt - timedelta(days=90)
    paid_totals = (
        select(
            SalesOrder.person_id.label("person_id"),
            func.coalesce(func.sum(SalesOrder.amount_paid), 0).label("total_paid"),
        )
        .where(
            SalesOrder.is_active.is_(True),
        )
        .group_by(SalesOrder.person_id)
        .subquery()
    )
    bandwidth_usage = (
        select(
            BandwidthSample.subscription_id.label("subscriber_id"),
            func.max(BandwidthSample.sample_at).label("last_bandwidth_usage_at"),
        )
        .group_by(BandwidthSample.subscription_id)
        .subquery()
    )
    service_event_usage = (
        select(
            EventStore.subscriber_id.label("subscriber_id"),
            func.max(EventStore.created_at).label("last_service_event_at"),
        )
        .where(
            EventStore.is_active.is_(True),
            EventStore.subscriber_id.isnot(None),
            EventStore.event_type.in_(["usage.recorded", "session.started", "session.ended", "device.online"]),
        )
        .group_by(EventStore.subscriber_id)
        .subquery()
    )
    last_seen_at_expr = case(
        (
            bandwidth_usage.c.last_bandwidth_usage_at.is_(None),
            service_event_usage.c.last_service_event_at,
        ),
        (
            service_event_usage.c.last_service_event_at.is_(None),
            bandwidth_usage.c.last_bandwidth_usage_at,
        ),
        (
            bandwidth_usage.c.last_bandwidth_usage_at >= service_event_usage.c.last_service_event_at,
            bandwidth_usage.c.last_bandwidth_usage_at,
        ),
        else_=service_event_usage.c.last_service_event_at,
    )
    rows = db.execute(
        select(
            Subscriber.id.label("subscriber_id"),
            Subscriber.subscriber_number,
            Subscriber.status,
            Subscriber.service_plan,
            activation_event_at.label("activation_event_at"),
            Person.first_name,
            Person.last_name,
            Person.display_name,
            paid_totals.c.total_paid,
            bandwidth_usage.c.last_bandwidth_usage_at,
            service_event_usage.c.last_service_event_at,
            last_seen_at_expr.label("last_seen_at"),
        )
        .outerjoin(Person, Person.id == Subscriber.person_id)
        .outerjoin(paid_totals, paid_totals.c.person_id == Subscriber.person_id)
        .outerjoin(bandwidth_usage, bandwidth_usage.c.subscriber_id == Subscriber.id)
        .outerjoin(service_event_usage, service_event_usage.c.subscriber_id == Subscriber.id)
        .where(
            activation_event_at.isnot(None),
            activation_event_at <= usage_cutoff,
            Subscriber.status != SubscriberStatus.pending,
            or_(last_seen_at_expr.is_(None), last_seen_at_expr < usage_cutoff),
        )
        .group_by(
            Subscriber.id,
            Subscriber.subscriber_number,
            Subscriber.status,
            Subscriber.service_plan,
            activation_event_at,
            Person.first_name,
            Person.last_name,
            Person.display_name,
            paid_totals.c.total_paid,
            bandwidth_usage.c.last_bandwidth_usage_at,
            service_event_usage.c.last_service_event_at,
            last_seen_at_expr,
        )
        .order_by(activation_event_at.asc(), Subscriber.subscriber_number.asc())
        .limit(limit)
    ).all()

    now = datetime.now(UTC)
    results = []
    for row in rows:
        raw_name = row.display_name or f"{row.first_name or ''} {row.last_name or ''}".strip() or row.subscriber_number
        activation_at = row.activation_event_at
        last_usage_at = row.last_seen_at
        tenure_days = (now.date() - activation_at.date()).days if activation_at else 0
        days_since_use = (now.date() - last_usage_at.date()).days if last_usage_at else tenure_days
        results.append(
            {
                "name": _clean_report_name(raw_name),
                "subscriber_number": row.subscriber_number or "",
                "plan": row.service_plan or "",
                "status": row.status.value if row.status else "unknown",
                "activated_at": activation_at.strftime("%Y-%m-%d") if activation_at else "",
                "last_usage_at": last_usage_at.strftime("%Y-%m-%d") if last_usage_at else "",
                "days_since_use": max(days_since_use, 90),
                "total_paid": round(float(row.total_paid or 0), 2),
            }
        )
    return results


def lifecycle_longest_tenure(db: Session, limit: int = 10) -> list[dict]:
    """Top subscribers by tenure (active only)."""
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    subs = db.execute(
        select(
            Subscriber.subscriber_number,
            Subscriber.service_plan,
            Subscriber.service_region,
            activation_event_at.label("activation_event_at"),
            Person.first_name,
            Person.last_name,
            Person.display_name,
            func.coalesce(func.sum(SalesOrder.amount_paid), 0).label("total_paid"),
        )
        .join(Person, Person.id == Subscriber.person_id)
        .outerjoin(
            SalesOrder,
            (SalesOrder.person_id == Subscriber.person_id)
            & SalesOrder.is_active.is_(True)
            & SalesOrder.status.in_([SalesOrderStatus.confirmed, SalesOrderStatus.paid, SalesOrderStatus.fulfilled]),
        )
        .where(
            Subscriber.is_active.is_(True),
            Subscriber.status == SubscriberStatus.active,
            activation_event_at.isnot(None),
        )
        .group_by(
            Subscriber.subscriber_number,
            Subscriber.service_plan,
            Subscriber.service_region,
            activation_event_at,
            Person.first_name,
            Person.last_name,
            Person.display_name,
        )
        .order_by(activation_event_at.asc())
        .limit(limit)
    ).all()

    now = datetime.now(UTC)
    results = []
    for row in subs:
        raw_name = row.display_name or f"{row.first_name or ''} {row.last_name or ''}".strip() or row.subscriber_number
        name = _clean_report_name(raw_name)
        tenure = (now.date() - row.activation_event_at.date()).days if row.activation_event_at else 0
        results.append(
            {
                "name": name,
                "subscriber_number": row.subscriber_number,
                "plan": row.service_plan or "",
                "region": row.service_region or "",
                "activated_at": row.activation_event_at.strftime("%Y-%m-%d") if row.activation_event_at else "",
                "tenure_days": tenure,
                "total_paid": round(float(row.total_paid or 0), 2),
            }
        )
    return results


def lifecycle_top_subscribers_by_value(db: Session, limit: int = 10) -> list[dict]:
    """Top subscribers of all time by realized paid amount on sales orders."""
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    rows = db.execute(
        select(
            Person.id.label("person_id"),
            Subscriber.subscriber_number,
            Subscriber.status,
            Subscriber.service_plan,
            activation_event_at.label("activation_event_at"),
            Person.first_name,
            Person.last_name,
            Person.display_name,
            func.coalesce(func.sum(SalesOrder.amount_paid), 0).label("total_paid"),
            func.count(SalesOrder.id).label("order_count"),
        )
        .join(Person, Person.id == Subscriber.person_id)
        .join(SalesOrder, SalesOrder.person_id == Subscriber.person_id)
        .where(
            SalesOrder.is_active.is_(True),
            SalesOrder.status.in_([SalesOrderStatus.confirmed, SalesOrderStatus.paid, SalesOrderStatus.fulfilled]),
        )
        .group_by(
            Person.id,
            Subscriber.subscriber_number,
            Subscriber.status,
            Subscriber.service_plan,
            activation_event_at,
            Person.first_name,
            Person.last_name,
            Person.display_name,
        )
        .order_by(func.coalesce(func.sum(SalesOrder.amount_paid), 0).desc(), func.count(SalesOrder.id).desc())
    ).all()

    now = datetime.now(UTC)
    deduped: dict[tuple[Any, str, str, float, int], dict[str, Any]] = {}
    for row in rows:
        raw_name = row.display_name or f"{row.first_name or ''} {row.last_name or ''}".strip() or row.subscriber_number
        clean_name = _clean_report_name(raw_name)
        total_paid = float(row.total_paid or 0)
        activation_at = row.activation_event_at
        tenure_days = (now.date() - activation_at.date()).days if activation_at else 0
        tenure_months = round(tenure_days / 30.4, 1) if tenure_days > 0 else 0
        avg_monthly_spend = round(total_paid / tenure_months, 2) if tenure_months > 0 else total_paid
        status = row.status.value if row.status else "unknown"
        candidate_subscriber_number = row.subscriber_number or ""
        dedupe_key = (
            row.person_id,
            status,
            activation_at.strftime("%Y-%m-%d") if activation_at else "",
            round(total_paid, 2),
            int(row.order_count or 0),
        )
        result_row = {
            "subscriber_id": str(row.person_id) if row.person_id else (candidate_subscriber_number or clean_name),
            "name": clean_name,
            "subscriber_number": candidate_subscriber_number,
            "plan": row.service_plan or "",
            "status": status,
            "activated_at": activation_at.strftime("%Y-%m-%d") if activation_at else "",
            "tenure_months": tenure_months,
            "order_count": int(row.order_count or 0),
            "total_paid": round(total_paid, 2),
            "avg_monthly_spend": avg_monthly_spend,
        }

        existing = deduped.get(dedupe_key)
        if existing is None:
            deduped[dedupe_key] = result_row
            continue

        existing_subscriber_number = existing["subscriber_number"] or ""
        candidate_is_name = candidate_subscriber_number.strip().lower() == clean_name.strip().lower()
        existing_is_name = existing_subscriber_number.strip().lower() == clean_name.strip().lower()
        candidate_digit_count = sum(ch.isdigit() for ch in candidate_subscriber_number)
        existing_digit_count = sum(ch.isdigit() for ch in existing_subscriber_number)
        if (existing_is_name and not candidate_is_name) or candidate_digit_count > existing_digit_count:
            existing["subscriber_number"] = candidate_subscriber_number

    results = list(deduped.values())
    results.sort(key=lambda row: (-row["total_paid"], -row["tenure_months"], row["name"]))
    results = results[:limit]
    return results


def lifecycle_top_subscribers_by_tenure_proxy(db: Session, limit: int = 10) -> list[dict]:
    """Top active subscribers ranked purely by tenure."""
    rows = _historical_subscriber_value_rows(db)
    return sorted(rows, key=lambda row: (-row["tenure_months"], -row["annualized_plan_estimate"]))[:limit]


def lifecycle_top_subscribers_by_estimated_plan_value(db: Session, limit: int = 10) -> list[dict]:
    """Top active subscribers ranked by estimated annualized plan value."""
    rows = _historical_subscriber_value_rows(db)
    return sorted(rows, key=lambda row: (-row["annualized_plan_estimate"], -row["tenure_months"]))[:limit]


def lifecycle_top_subscribers_by_hybrid_score(db: Session, limit: int = 10) -> list[dict]:
    """Top active subscribers ranked by hybrid tenure x plan value score."""
    rows = _historical_subscriber_value_rows(db)
    return sorted(rows, key=lambda row: (-row["hybrid_score"], -row["tenure_months"]))[:limit]


def _historical_subscriber_value_rows(db: Session) -> list[dict]:
    """Historical active-subscriber leaderboard rows using subscriber data available back to inception."""
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    rows = db.execute(
        select(
            Subscriber.id,
            Subscriber.subscriber_number,
            Subscriber.status,
            Subscriber.service_plan,
            Subscriber.service_speed,
            activation_event_at.label("activation_event_at"),
            Person.first_name,
            Person.last_name,
            Person.display_name,
        )
        .join(Person, Person.id == Subscriber.person_id)
        .where(
            Subscriber.is_active.is_(True),
            Subscriber.status == SubscriberStatus.active,
            activation_event_at.isnot(None),
        )
    ).all()

    now = datetime.now(UTC)
    results: list[dict] = []
    for row in rows:
        raw_name = row.display_name or f"{row.first_name or ''} {row.last_name or ''}".strip() or row.subscriber_number
        tenure_days = (now.date() - row.activation_event_at.date()).days if row.activation_event_at else 0
        tenure_months = round(tenure_days / 30.4, 1) if tenure_days > 0 else 0
        monthly_plan_estimate = _estimate_monthly_plan_value(row.service_plan, row.service_speed)
        annualized_plan_estimate = round(monthly_plan_estimate * 12, 2)
        hybrid_score = round(monthly_plan_estimate * tenure_months, 2)
        results.append(
            {
                "subscriber_id": str(row.id),
                "subscriber_number": row.subscriber_number or "",
                "name": _clean_report_name(raw_name),
                "activated_at": row.activation_event_at.strftime("%Y-%m-%d") if row.activation_event_at else "",
                "tenure_months": tenure_months,
                "status": row.status.value if row.status else "unknown",
                "service_plan": row.service_plan or "",
                "service_speed": row.service_speed or "",
                "monthly_plan_estimate": monthly_plan_estimate,
                "annualized_plan_estimate": annualized_plan_estimate,
                "hybrid_score": hybrid_score,
            }
        )
    return results


def _estimate_monthly_plan_value(service_plan: str | None, service_speed: str | None) -> float:
    """Estimate a monthly plan value from plan metadata when billing history is unavailable."""
    speed_mbps = _speed_mbps_value(service_speed)
    plan_text = (service_plan or "").lower()

    if speed_mbps is not None:
        if speed_mbps <= 10:
            return 10000.0
        if speed_mbps <= 20:
            return 15000.0
        if speed_mbps <= 50:
            return 25000.0
        if speed_mbps <= 100:
            return 45000.0
        if speed_mbps <= 200:
            return 70000.0
        if speed_mbps <= 500:
            return 120000.0
        return 200000.0

    if "enterprise" in plan_text:
        return 180000.0
    if "business" in plan_text:
        return 90000.0
    if "fiber" in plan_text or "premium" in plan_text:
        return 50000.0
    if "home" in plan_text or "residential" in plan_text:
        return 20000.0
    return 15000.0


def _speed_mbps_value(service_speed: str | None) -> float | None:
    """Extract an Mbps figure from subscriber.service_speed."""
    if not service_speed:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", service_speed.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _churn_event_at():
    return func.coalesce(
        Subscriber.terminated_at,
        case(
            (Subscriber.is_active.is_(False), Subscriber.updated_at),
            else_=None,
        ),
    )


def _strict_churn_event_at():
    return func.coalesce(
        Subscriber.terminated_at,
        case(
            (
                Subscriber.is_active.is_(False),
                case(
                    (Subscriber.status == SubscriberStatus.terminated, Subscriber.updated_at),
                    else_=None,
                ),
            ),
            else_=None,
        ),
    )


# =====================================================================
# Report 3: Service Quality
# =====================================================================


def service_quality_kpis(db: Session, start_dt: datetime, end_dt: datetime) -> dict:
    """5 KPI cards for service quality."""
    open_statuses = [TicketStatus.new, TicketStatus.open, TicketStatus.pending]

    subs_with_open_tickets = (
        db.scalar(
            select(func.count(func.distinct(Ticket.subscriber_id))).where(
                Ticket.is_active.is_(True),
                Ticket.subscriber_id.isnot(None),
                Ticket.status.in_(open_statuses),
            )
        )
        or 0
    )

    # Avg resolution time
    avg_res = db.scalar(
        select(func.avg(func.extract("epoch", Ticket.resolved_at - Ticket.created_at) / 3600)).where(
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
    active_wo = (
        db.scalar(
            select(func.count(WorkOrder.id)).where(
                WorkOrder.is_active.is_(True),
                WorkOrder.status.notin_([WorkOrderStatus.completed, WorkOrderStatus.canceled]),
                WorkOrder.created_at >= start_dt,
                WorkOrder.created_at <= end_dt,
            )
        )
        or 0
    )

    # SLA compliance
    total_sla = (
        db.scalar(
            select(func.count(TicketSlaEvent.id)).where(
                TicketSlaEvent.expected_at.isnot(None),
                TicketSlaEvent.actual_at.isnot(None),
                TicketSlaEvent.created_at >= start_dt,
                TicketSlaEvent.created_at <= end_dt,
            )
        )
        or 0
    )
    met_sla = (
        db.scalar(
            select(func.count(TicketSlaEvent.id)).where(
                TicketSlaEvent.expected_at.isnot(None),
                TicketSlaEvent.actual_at.isnot(None),
                TicketSlaEvent.actual_at <= TicketSlaEvent.expected_at,
                TicketSlaEvent.created_at >= start_dt,
                TicketSlaEvent.created_at <= end_dt,
            )
        )
        or 0
    )
    if total_sla == 0:
        completion_event_at = func.coalesce(Ticket.closed_at, Ticket.resolved_at)
        total_sla = (
            db.scalar(
                select(func.count(Ticket.id)).where(
                    Ticket.is_active.is_(True),
                    Ticket.due_at.isnot(None),
                    completion_event_at.isnot(None),
                    Ticket.created_at >= start_dt,
                    Ticket.created_at <= end_dt,
                )
            )
            or 0
        )
        met_sla = (
            db.scalar(
                select(func.count(Ticket.id)).where(
                    Ticket.is_active.is_(True),
                    Ticket.due_at.isnot(None),
                    completion_event_at.isnot(None),
                    completion_event_at <= Ticket.due_at,
                    Ticket.created_at >= start_dt,
                    Ticket.created_at <= end_dt,
                )
            )
            or 0
        )

    sla_compliance = round(met_sla / total_sla * 100, 1) if total_sla > 0 else 0

    return {
        "subs_with_open_tickets": subs_with_open_tickets,
        "avg_resolution_hrs": avg_resolution_hrs,
        "repeat_contact_rate": repeat_rate,
        "active_work_orders": active_wo,
        "sla_compliance": sla_compliance,
    }


def service_quality_tickets_by_type(db: Session, start_dt: datetime, end_dt: datetime) -> dict[str, int]:
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


def service_quality_wo_by_type(db: Session, start_dt: datetime, end_dt: datetime) -> dict[str, int]:
    """Work order type distribution."""
    rows = db.execute(
        select(WorkOrder.work_type, func.count(WorkOrder.id))
        .where(
            WorkOrder.is_active.is_(True),
            WorkOrder.created_at >= start_dt,
            WorkOrder.created_at <= end_dt,
        )
        .group_by(WorkOrder.work_type)
    ).all()
    return {(t.value if t else "other"): c for t, c in rows}


def service_quality_weekly_trend(db: Session, start_dt: datetime, end_dt: datetime) -> list[dict]:
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


def service_quality_high_maintenance(db: Session, start_dt: datetime, end_dt: datetime, limit: int = 10) -> list[dict]:
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
        .outerjoin(Person, Person.id == Subscriber.person_id)
        .where(Subscriber.id.in_(sub_ids))
    ).all()
    sub_map = {s[0]: s for s in subs}

    results = []
    aggregated: dict[str, dict] = {}
    for sid, tickets, wos, projects, total in ranked:
        s = sub_map.get(sid)
        if not s:
            continue
        raw_name = s.display_name or f"{s.first_name or ''} {s.last_name or ''}".strip() or s.subscriber_number
        name = _clean_report_name(raw_name)
        dedupe_key = name.casefold()
        existing = aggregated.get(dedupe_key)
        if existing:
            existing["tickets"] += tickets
            existing["work_orders"] += wos
            existing["projects"] += projects
            existing["total"] += total
            if not existing["subscriber_number"] and s.subscriber_number:
                existing["subscriber_number"] = s.subscriber_number
            if not existing["region"] and s.service_region:
                existing["region"] = s.service_region
            if not existing["plan"] and s.service_plan:
                existing["plan"] = s.service_plan
            continue

        aggregated[dedupe_key] = {
            "name": name,
            "subscriber_number": s.subscriber_number,
            "region": s.service_region or "",
            "plan": s.service_plan or "",
            "tickets": tickets,
            "work_orders": wos,
            "projects": projects,
            "total": total,
        }

    results = sorted(
        aggregated.values(),
        key=lambda row: (-row["total"], -row["tickets"], row["name"]),
    )[:limit]
    return results


def service_quality_regional(db: Session, start_dt: datetime, end_dt: datetime) -> list[dict]:
    """Regional service quality metrics."""
    subscriber_region_key = func.nullif(func.trim(Subscriber.service_region), "").label("region")

    active_rows = db.execute(
        select(subscriber_region_key, func.count(Subscriber.id))
        .where(
            Subscriber.is_active.is_(True),
            Subscriber.status == SubscriberStatus.active,
            subscriber_region_key.isnot(None),
        )
        .group_by(subscriber_region_key)
    ).all()

    if not active_rows:
        return []

    ticket_rows = db.execute(
        select(subscriber_region_key, func.count(Ticket.id))
        .join(Subscriber, Subscriber.id == Ticket.subscriber_id)
        .where(
            Ticket.is_active.is_(True),
            subscriber_region_key.isnot(None),
            Ticket.created_at >= start_dt,
            Ticket.created_at <= end_dt,
        )
        .group_by(subscriber_region_key)
    ).all()

    avg_res_rows = db.execute(
        select(subscriber_region_key, func.avg(func.extract("epoch", Ticket.resolved_at - Ticket.created_at) / 3600))
        .join(Subscriber, Subscriber.id == Ticket.subscriber_id)
        .where(
            Ticket.is_active.is_(True),
            Ticket.resolved_at.isnot(None),
            subscriber_region_key.isnot(None),
            Ticket.created_at >= start_dt,
            Ticket.created_at <= end_dt,
        )
        .group_by(subscriber_region_key)
    ).all()

    wo_region_key = func.coalesce(
        func.nullif(func.trim(Project.region), ""),
        func.nullif(func.trim(Subscriber.service_region), ""),
    ).label("region")
    wo_rows = db.execute(
        select(wo_region_key, func.count(WorkOrder.id))
        .outerjoin(Project, Project.id == WorkOrder.project_id)
        .outerjoin(Subscriber, Subscriber.id == WorkOrder.subscriber_id)
        .where(
            WorkOrder.is_active.is_(True),
            wo_region_key.isnot(None),
            WorkOrder.created_at >= start_dt,
            WorkOrder.created_at <= end_dt,
        )
        .group_by(wo_region_key)
    ).all()

    aggregated: dict[str, dict] = {}

    def _bucket_for(raw_region: str | None) -> dict:
        normalized_region = _normalize_region_name(raw_region)
        return aggregated.setdefault(
            normalized_region,
            {
                "region": normalized_region,
                "active_subscribers": 0,
                "ticket_count": 0,
                "resolution_total": 0.0,
                "resolution_samples": 0,
                "wo_count": 0,
            },
        )

    for raw_region, count in active_rows:
        _bucket_for(raw_region)["active_subscribers"] += int(count or 0)

    for raw_region, count in ticket_rows:
        _bucket_for(raw_region)["ticket_count"] += int(count or 0)

    for raw_region, avg_res in avg_res_rows:
        if avg_res is None:
            continue
        bucket = _bucket_for(raw_region)
        bucket["resolution_total"] += float(avg_res)
        bucket["resolution_samples"] += 1

    for raw_region, count in wo_rows:
        _bucket_for(raw_region)["wo_count"] += int(count or 0)

    results = []
    for row in aggregated.values():
        active_subscribers = row["active_subscribers"]
        avg_tickets = round(row["ticket_count"] / active_subscribers, 2) if active_subscribers > 0 else 0
        avg_res_hrs = (
            round(row["resolution_total"] / row["resolution_samples"], 1) if row["resolution_samples"] > 0 else 0
        )
        results.append(
            {
                "region": row["region"],
                "active_subscribers": active_subscribers,
                "avg_tickets_per_sub": avg_tickets,
                "avg_resolution_hrs": avg_res_hrs,
                "wo_count": row["wo_count"],
            }
        )

    results.sort(key=lambda x: (-x["active_subscribers"], -x["wo_count"], x["region"]))
    return results


# =====================================================================
# Report 4: Revenue & Pipeline
# =====================================================================


def revenue_kpis(db: Session, start_dt: datetime, end_dt: datetime) -> dict:
    """5 KPI cards for revenue report."""
    total_value = db.scalar(
        select(func.coalesce(func.sum(SalesOrder.total), 0)).where(
            SalesOrder.is_active.is_(True),
            SalesOrder.created_at >= start_dt,
            SalesOrder.created_at <= end_dt,
        )
    ) or Decimal("0")

    order_count = (
        db.scalar(
            select(func.count(SalesOrder.id)).where(
                SalesOrder.is_active.is_(True),
                SalesOrder.created_at >= start_dt,
                SalesOrder.created_at <= end_dt,
            )
        )
        or 0
    )

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

    pending_fulfillment = (
        db.scalar(
            select(func.count(SalesOrder.id)).where(
                SalesOrder.is_active.is_(True),
                SalesOrder.status.in_([SalesOrderStatus.confirmed, SalesOrderStatus.paid]),
            )
        )
        or 0
    )

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
    return [{"month": row[0].strftime("%Y-%m"), "total": float(row[1])} for row in rows if row[0]]


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


def revenue_top_subscribers(db: Session, start_dt: datetime, end_dt: datetime, limit: int = 20) -> list[dict]:
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
        select(Person.id, Person.display_name, Person.first_name, Person.last_name, Person.email).where(
            Person.id.in_(person_ids)
        )
    ).all()
    person_map = {p[0]: p for p in people}

    # Get subscriber status for these people
    sub_statuses = db.execute(
        select(Subscriber.person_id, Subscriber.status).where(
            Subscriber.is_active.is_(True), Subscriber.person_id.in_(person_ids)
        )
    ).all()
    sub_status_map = {r[0]: r[1].value if r[1] else "unknown" for r in sub_statuses}

    results = []
    for row in rows:
        p = person_map.get(row[0])
        if not p:
            continue
        name = p.display_name or f"{p.first_name or ''} {p.last_name or ''}".strip() or "Unknown"
        results.append(
            {
                "name": name,
                "email": p.email or "",
                "total_revenue": float(row.total_revenue),
                "order_count": row.order_count,
                "avg_value": round(float(row.avg_value), 2),
                "latest_order": row.latest_order.strftime("%Y-%m-%d") if row.latest_order else "",
                "status": sub_status_map.get(row[0], "N/A"),
            }
        )
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
        results.append(
            {
                "order_number": row.order_number or "",
                "customer": name,
                "total": float(row.total),
                "paid": float(row.amount_paid),
                "balance": float(row.balance_due),
                "due_date": row.payment_due_date.strftime("%Y-%m-%d") if row.payment_due_date else "",
                "days_overdue": days_overdue,
            }
        )
    return results
