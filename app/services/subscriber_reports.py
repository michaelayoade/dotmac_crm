"""Subscriber report service functions for reports 1-4."""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
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
    cutoff = datetime.now(UTC) - timedelta(days=365)
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
    return [{"month": str(row.month)[:7], "count": int(row.total_churn or 0)} for row in rows if row.month]


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


def lifecycle_recent_churns(db: Session, limit: int = 5) -> list[dict]:
    """Recently churned subscribers in the last 30 days."""
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    churn_event_at = _churn_event_at()
    cutoff = datetime.now(UTC) - timedelta(days=30)
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
        .join(Person, Person.id == Subscriber.person_id)
        .where(
            churn_event_at.isnot(None),
            churn_event_at >= cutoff,
        )
        .order_by(churn_event_at.desc())
        .limit(limit)
    ).all()

    results = []
    for row in subs:
        name = row.display_name or f"{row.first_name or ''} {row.last_name or ''}".strip() or "Unknown"
        tenure = 0
        if row.activation_event_at and row.churn_event_at:
            tenure = (row.churn_event_at.date() - row.activation_event_at.date()).days
        results.append(
            {
                "name": name,
                "subscriber_number": row.subscriber_number,
                "plan": row.service_plan or "",
                "region": row.service_region or "",
                "activated_at": row.activation_event_at.strftime("%Y-%m-%d") if row.activation_event_at else "",
                "terminated_at": row.churn_event_at.strftime("%Y-%m-%d") if row.churn_event_at else "",
                "tenure_days": tenure,
            }
        )
    return results


def churned_subscribers_kpis(db: Session, start_dt: datetime, end_dt: datetime) -> dict:
    """Summary metrics for churned subscribers in a date range."""
    rows = churned_subscribers_rows(db, start_dt, end_dt, limit=5000)
    churned_count = len(rows)
    avg_tenure_days = round(sum(row["tenure_days"] for row in rows) / churned_count, 1) if churned_count else 0
    impacted_regions = len({row["region"] for row in rows if row["region"] and row["region"] != "Unknown"})
    impacted_plans = len({row["plan"] for row in rows if row["plan"]})
    return {
        "churned_count": churned_count,
        "avg_tenure_days": avg_tenure_days,
        "impacted_regions": impacted_regions,
        "impacted_plans": impacted_plans,
    }


def churned_subscribers_trend(db: Session, start_dt: datetime, end_dt: datetime) -> list[dict]:
    """Daily churn count in the selected date range."""
    churn_event_at = _churn_event_at()
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
    churn_event_at = _churn_event_at()
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
        )
        .join(Person, Person.id == Subscriber.person_id)
        .where(
            Subscriber.is_active.is_(True),
            Subscriber.status == SubscriberStatus.active,
            activation_event_at.isnot(None),
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
            }
        )
    return results


def _churn_event_at():
    return func.coalesce(
        Subscriber.terminated_at,
        case(
            (Subscriber.is_active.is_(False), Subscriber.updated_at),
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
