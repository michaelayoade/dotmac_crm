"""Subscriber report service functions for reports 1-4."""

import re
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, TypedDict

from sqlalchemy import Date, DateTime, Integer, Numeric, String, and_, case, cast, false, func, or_, select, text, true
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.bandwidth import BandwidthSample
from app.models.crm.enums import LeadStatus
from app.models.crm.sales import Lead
from app.models.event_store import EventStore
from app.models.person import ChannelType as PersonChannelType
from app.models.person import Person, PersonChannel
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


def _coerce_datetime_utc(value: datetime | date | None) -> datetime | None:
    """Normalize date-like values to timezone-aware UTC datetimes."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=UTC)
    return None


def _date_value(value: datetime | date | None) -> date | None:
    normalized = _coerce_datetime_utc(value)
    return normalized.date() if normalized is not None else None


def _format_date_value(value: datetime | date | None) -> str:
    date_value = _date_value(value)
    return date_value.strftime("%Y-%m-%d") if date_value is not None else ""


def _metadata_text(metadata: Mapping[str, Any] | None, key: str) -> str:
    if not isinstance(metadata, Mapping):
        return ""
    value = metadata.get(key)
    if value is None:
        return ""
    text = str(value).strip()
    return text


def _parse_iso_date_text(value: str) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


_SCI_NOTATION_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?E[+-]?\d+$", re.IGNORECASE)
_NOISE_NUMERIC_ONLY_MIN_DIGITS = 8
_NOISE_MIXED_MIN_DIGITS = 6
_NOISE_MIN_ALPHA_RATIO = 0.25


def _looks_like_noise_name(value: str | None) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return True
    if _SCI_NOTATION_RE.match(text.replace(" ", "")):
        return True
    alnum = sum(1 for ch in text if ch.isalnum())
    alpha = sum(1 for ch in text if ch.isalpha())
    digit = sum(1 for ch in text if ch.isdigit())
    if alnum == 0:
        return True
    if alpha == 0 and digit >= _NOISE_NUMERIC_ONLY_MIN_DIGITS:
        return True
    return alpha > 0 and alpha / max(alnum, 1) < _NOISE_MIN_ALPHA_RATIO and digit >= _NOISE_MIXED_MIN_DIGITS


def _dedupe_churn_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the single most risky row per linked person."""
    if not rows:
        return rows

    risk_rank = {
        "Churned": 4,
        "Suspended": 3,
        "Overdue": 2,
        "Due Soon": 1,
        "Pending": 0,
    }

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        person_id = str(row.get("_person_id") or "").strip()
        external_id = str(row.get("_external_id") or "").strip()
        subscriber_number = str(row.get("_subscriber_number") or "").strip()
        subscriber_id = str(row.get("subscriber_id") or "").strip()
        key = person_id or external_id or subscriber_number or subscriber_id
        grouped[key].append(row)

    def sort_key(row: dict[str, Any]) -> tuple:
        segment = str(row.get("risk_segment") or "")
        due = row.get("days_to_due")
        due_value = due if isinstance(due, int) else 10**9
        last_synced = str(row.get("_last_synced_at") or "")
        return (
            -risk_rank.get(segment, -1),
            -float(row.get("balance") or 0.0),
            due_value,
            last_synced,
        )

    deduped: list[dict[str, Any]] = []
    for bucket in grouped.values():
        bucket.sort(key=sort_key)
        deduped.append(bucket[0])
    return deduped


def overview_regional_breakdown(
    db: Session,
    start_dt: datetime,
    end_dt: datetime,
    subscriber_ids: list | None = None,
) -> list[dict[str, Any]]:
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


def _lifecycle_unified_churn_metrics(
    db: Session,
    start_dt: datetime,
    end_dt: datetime,
    inactivity_threshold_days: int = 40,
) -> dict[str, float | int]:
    """Unified churn metrics for the lifecycle KPI card."""

    def _coerce_date(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day") and not isinstance(value, str):
            return value
        try:
            return datetime.fromisoformat(str(value)).date()
        except ValueError:
            return None

    def _coerce_utc_datetime(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return None

    def _clean_balance_decimal(raw_value) -> Decimal:
        cleaned = re.sub(r"[^0-9.\-]", "", str(raw_value or ""))
        if not cleaned:
            return Decimal("0")
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return Decimal("0")

    dialect_name = db.get_bind().dialect.name if db.get_bind() is not None else ""
    threshold_days = max(1, int(inactivity_threshold_days))
    if dialect_name == "postgresql":
        result = (
            db.execute(
                text(
                    """
                WITH params AS (
                    SELECT
                        CAST(:start_dt AS timestamptz) AS start_dt,
                        CAST(:end_dt AS timestamptz) AS end_dt,
                        CAST(:inactivity_threshold_days AS integer) AS inactivity_threshold_days
                ),
                source_subscribers AS (
                    SELECT
                        s.id AS subscriber_id,
                        COALESCE(s.activated_at, s.created_at)::timestamptz AS active_start_at,
                        s.activated_at::timestamptz AS activated_at,
                        s.terminated_at::timestamptz AS terminated_at,
                        CAST(NULLIF(s.sync_metadata ->> 'last_transaction_date', '') AS date) AS last_payment_date,
                        CAST(s.next_bill_date AS date) AS next_bill_date,
                        CAST(
                            NULLIF(REGEXP_REPLACE(COALESCE(s.balance, ''), '[^0-9.\\-]', '', 'g'), '')
                            AS numeric
                        ) AS balance_num
                    FROM subscribers s
                ),
                active_at_start AS (
                    SELECT
                        ss.subscriber_id,
                        ss.activated_at,
                        ss.terminated_at,
                        ss.last_payment_date,
                        ss.next_bill_date,
                        ss.balance_num
                    FROM source_subscribers ss
                    CROSS JOIN params p
                    WHERE ss.active_start_at IS NOT NULL
                      AND ss.active_start_at <= p.start_dt
                      AND (ss.terminated_at IS NULL OR ss.terminated_at > p.start_dt)
                ),
                churned_in_period AS (
                    SELECT
                        CASE
                            WHEN a.terminated_at >= p.start_dt AND a.terminated_at <= p.end_dt THEN 'operational'
                            WHEN a.last_payment_date IS NOT NULL
                                 AND a.last_payment_date < (p.end_dt::date - make_interval(days => p.inactivity_threshold_days))
                                THEN 'behavioral'
                            WHEN a.next_bill_date IS NOT NULL
                                 AND a.next_bill_date < p.end_dt::date
                                 AND COALESCE(a.balance_num, 0) > 0
                                THEN 'behavioral'
                            ELSE NULL
                        END AS churn_type,
                        a.subscriber_id
                    FROM active_at_start a
                    CROSS JOIN params p
                ),
                churn_counts AS (
                    SELECT
                        COUNT(c.subscriber_id) FILTER (WHERE c.churn_type IS NOT NULL) AS total_churned_subscribers,
                        COUNT(c.subscriber_id) FILTER (WHERE c.churn_type = 'operational') AS count_operational_churn,
                        COUNT(c.subscriber_id) FILTER (WHERE c.churn_type = 'behavioral') AS count_behavioral_churn
                    FROM churned_in_period c
                ),
                active_counts AS (
                    SELECT
                        COUNT(a.subscriber_id) AS total_active_subscribers_start
                    FROM active_at_start a
                )
                SELECT
                    cc.total_churned_subscribers,
                    cc.count_operational_churn,
                    cc.count_behavioral_churn,
                    ac.total_active_subscribers_start,
                    CASE
                        WHEN ac.total_active_subscribers_start > 0
                        THEN cc.total_churned_subscribers::numeric / ac.total_active_subscribers_start::numeric
                        ELSE 0::numeric
                    END AS churn_rate_ratio
                FROM churn_counts cc
                CROSS JOIN active_counts ac
                """
                ),
                {
                    "start_dt": start_dt,
                    "end_dt": end_dt,
                    "inactivity_threshold_days": threshold_days,
                },
            )
            .mappings()
            .first()
        )
        if result is None:
            return {
                "total_churned_subscribers": 0,
                "count_operational_churn": 0,
                "count_behavioral_churn": 0,
                "total_active_subscribers_start": 0,
                "churn_rate_ratio": 0.0,
            }
        return {
            "total_churned_subscribers": int(result["total_churned_subscribers"] or 0),
            "count_operational_churn": int(result["count_operational_churn"] or 0),
            "count_behavioral_churn": int(result["count_behavioral_churn"] or 0),
            "total_active_subscribers_start": int(result["total_active_subscribers_start"] or 0),
            "churn_rate_ratio": float(result["churn_rate_ratio"] or 0),
        }

    active_rows = db.execute(
        select(
            Subscriber.id,
            Subscriber.terminated_at,
            Subscriber.next_bill_date,
            Subscriber.balance,
            _sync_metadata_date_expr(db, "last_transaction_date").label("last_transaction_date"),
        ).where(
            func.coalesce(Subscriber.activated_at, Subscriber.created_at).isnot(None),
            func.coalesce(Subscriber.activated_at, Subscriber.created_at) <= start_dt,
            ((Subscriber.terminated_at.is_(None)) | (Subscriber.terminated_at > start_dt)),
        )
    ).all()

    active_ids = {row.id for row in active_rows}
    operational_ids = {
        row.id
        for row in active_rows
        if (terminated_at := _coerce_utc_datetime(row.terminated_at)) is not None
        and start_dt <= terminated_at <= end_dt
    }
    inactivity_cutoff = end_dt.date() - timedelta(days=threshold_days)
    behavioral_ids = {
        row.id
        for row in active_rows
        if row.id not in operational_ids
        and (
            (
                (last_transaction_date := _coerce_date(row.last_transaction_date)) is not None
                and last_transaction_date < inactivity_cutoff
            )
            or (
                (next_bill_date := _coerce_date(row.next_bill_date)) is not None
                and next_bill_date < end_dt.date()
                and _clean_balance_decimal(row.balance) > 0
            )
        )
    }
    total_churned_ids = operational_ids | behavioral_ids
    return {
        "total_churned_subscribers": len(total_churned_ids),
        "count_operational_churn": len(operational_ids),
        "count_behavioral_churn": len(behavioral_ids),
        "total_active_subscribers_start": len(active_ids),
        "churn_rate_ratio": (float(len(total_churned_ids)) / float(len(active_ids))) if active_ids else 0.0,
    }


def lifecycle_kpis(db: Session, start_dt: datetime, end_dt: datetime) -> dict:
    """5 KPI cards for lifecycle report."""
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)

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

    churn_metrics = _lifecycle_unified_churn_metrics(db, start_dt, end_dt)
    active_at_start = int(churn_metrics["total_active_subscribers_start"])
    operational_churn_in_period = int(churn_metrics["count_operational_churn"])
    behavioral_churn_in_period = int(churn_metrics["count_behavioral_churn"])
    terminated_in_period = int(churn_metrics["total_churned_subscribers"])
    churn_rate_ratio = float(churn_metrics["churn_rate_ratio"])
    churn_rate = round(churn_rate_ratio * 100, 5)

    churn_rows = _lifecycle_churn_rows(db, start_dt, end_dt, strict_terminated_only=True)
    avg_lifecycle_days = round(sum(row["tenure_days"] for row in churn_rows) / len(churn_rows), 1) if churn_rows else 0
    avg_lifecycle_months = round(avg_lifecycle_days / 30.4, 1) if avg_lifecycle_days > 0 else 0
    churn_event_at = _churn_event_at(db)

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
        "churn_rate_ratio": churn_rate_ratio,
        "terminated_in_period": terminated_in_period,
        "total_active_subscribers_start": active_at_start,
        "operational_churn_in_period": operational_churn_in_period,
        "behavioral_churn_in_period": behavioral_churn_in_period,
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
    rows = db.execute(
        select(
            Subscriber.status,
            Subscriber.is_active,
            Subscriber.terminated_at,
            Subscriber.updated_at,
        ).where(
            Subscriber.status == SubscriberStatus.terminated,
            or_(
                Subscriber.terminated_at.isnot(None),
                Subscriber.is_active.is_(False),
            ),
        )
    ).all()
    counts_by_month: dict[str, int] = {}
    for row in rows:
        event_at = row.terminated_at
        if event_at is None and row.status == SubscriberStatus.terminated and row.is_active is False:
            event_at = row.updated_at
        normalized_event = _coerce_datetime_utc(event_at)
        if normalized_event is None or normalized_event < cutoff:
            continue
        month_key = _month_start(normalized_event).strftime("%Y-%m")
        counts_by_month[month_key] = counts_by_month.get(month_key, 0) + 1

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
    churn_event_at = _churn_event_at(db)
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
        activation_at = _coerce_datetime_utc(row.activation_event_at)
        if activation_at is None:
            continue
        cohort_key = _month_start(activation_at).strftime("%Y-%m")
        cohorts[cohort_key].append(
            {
                "activation_event_at": activation_at,
                "churn_event_at": _coerce_datetime_utc(row.churn_event_at),
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


def lifecycle_recent_churn_summary(db: Session) -> dict[str, float | int]:
    """Recent churn metric for the last 30 days with previous-period comparison."""
    now = datetime.now(UTC)
    current_start = now - timedelta(days=30)
    previous_start = current_start - timedelta(days=30)

    current_count = _lifecycle_churn_count(db, current_start, now)
    previous_count = _lifecycle_churn_count(db, previous_start, current_start)
    if previous_count > 0:
        pct_change = round(((current_count - previous_count) / previous_count) * 100, 1)
    elif current_count > 0:
        pct_change = 100.0
    else:
        pct_change = 0.0
    return {
        "count": current_count,
        "previous_count": previous_count,
        "pct_change": pct_change,
    }


def _lifecycle_churn_count(db: Session, start_dt: datetime, end_dt: datetime) -> int:
    """Count unified churn rows for a date range."""
    if db is None:
        return 0
    churn_event_at = _churn_event_at(db)
    return (
        db.scalar(
            select(func.count(Subscriber.id)).where(
                churn_event_at.isnot(None),
                churn_event_at >= start_dt,
                churn_event_at <= end_dt,
            )
        )
        or 0
    )


def _lifecycle_churn_rows(
    db: Session,
    start_dt: datetime,
    end_dt: datetime,
    limit: int | None = None,
    strict_terminated_only: bool = False,
) -> list[dict]:
    """Build churn rows for the selected date range."""
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    churn_event_at = _strict_churn_event_at() if strict_terminated_only else _churn_event_at(db)
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
        activation_date = _date_value(row.activation_event_at)
        churn_date = _date_value(row.churn_event_at)
        tenure = max(0, (churn_date - activation_date).days) if activation_date and churn_date else 0
        results.append(
            {
                "name": name,
                "subscriber_number": row.subscriber_number or "",
                "plan": row.service_plan or "",
                "region": row.service_region or "",
                "activated_at": _format_date_value(row.activation_event_at),
                "terminated_at": _format_date_value(row.churn_event_at),
                "tenure_days": tenure,
            }
        )
    return results


def _month_start(value: datetime | date) -> datetime:
    normalized = _coerce_datetime_utc(value)
    if normalized is None:
        raise ValueError("Expected a date-like value")
    return normalized.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_months(value: datetime | date, months: int) -> datetime:
    normalized = _month_start(value)
    month_index = (normalized.year * 12 + (normalized.month - 1)) + months
    year = month_index // 12
    month = month_index % 12 + 1
    return normalized.replace(year=year, month=month, day=1)


def _month_end(value: datetime | date) -> datetime:
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


def _ensure_utc(value: datetime | date) -> datetime:
    normalized = _coerce_datetime_utc(value)
    if normalized is None:
        raise ValueError("Expected a date-like value")
    return normalized


def _days_since_expr(db: Session, column):
    """Portable days-since-date expression for SQLite and PostgreSQL."""
    dialect_name = db.get_bind().dialect.name if db.get_bind() is not None else ""
    if dialect_name == "sqlite":
        return cast(func.julianday(func.current_date()) - func.julianday(func.date(column)), Integer)
    return cast(func.current_date() - cast(column, Date), Integer)


def _parse_balance_amount(raw_value: object) -> float:
    text = str(raw_value or "").strip()
    if not text:
        return 0.0
    cleaned = re.sub(r"[^0-9.\-]", "", text.replace(",", ""))
    if not cleaned:
        return 0.0
    try:
        return round(float(Decimal(cleaned)), 2)
    except (InvalidOperation, ValueError):
        return 0.0


def get_churn_table(
    db: Session,
    due_soon_days: int = 7,
    *,
    high_balance_only: bool = False,
    segment: str | None = None,
    segments: list[str] | None = None,
    days_past_due: str | None = None,
    source: str = "local",
    limit: int = 500,
    enrich_visible_rows: bool = True,
) -> list[dict]:
    """Subscribers with non-current Splynx billing state, segmented by due/risk status."""

    def _normalize_segment(value: str | None) -> str | None:
        normalized_segment = (value or "").strip().lower()
        if normalized_segment in {"due_soon", "due soon"}:
            return "Due Soon"
        if normalized_segment == "overdue":
            return "Overdue"
        if normalized_segment == "suspended":
            return "Suspended"
        if normalized_segment == "churned":
            return "Churned"
        if normalized_segment == "pending":
            return "Pending"
        return None

    selected_segments: set[str] = set()
    normalized_single = _normalize_segment(segment)
    if normalized_single is not None:
        selected_segments.add(normalized_single)
    for raw_segment in segments or []:
        for candidate in str(raw_segment).split(","):
            normalized = _normalize_segment(candidate)
            if normalized is not None:
                selected_segments.add(normalized)

    normalized_days_past_due = (days_past_due or "").strip().lower().replace("_", "-")
    if normalized_days_past_due in {"current", "0"}:
        selected_days_past_due_category = "0"
    elif normalized_days_past_due in {"1-7", "1-to-7", "1 to 7", "within-7", "within7"}:
        selected_days_past_due_category = "1-7"
    elif normalized_days_past_due in {"8-30", "8-to-30", "8 to 30"}:
        selected_days_past_due_category = "8-30"
    elif normalized_days_past_due in {"31+", "31-plus", "31-and-above", "over30", "over-30", "31"}:
        selected_days_past_due_category = "31+"
    else:
        selected_days_past_due_category = None

    def _days_past_due_bucket(value: int | None) -> str | None:
        if value is None:
            return None
        if value <= 0:
            return "0"
        if value <= 7:
            return "1-7"
        if value <= 30:
            return "8-30"
        return "31+"

    def _matches_days_past_due_bucket(value: int | None) -> bool:
        if selected_days_past_due_category is None:
            return True
        return _days_past_due_bucket(value) == selected_days_past_due_category

    if source == "splynx_live":
        from app.services.splynx import (
            fetch_customer_billing,
            fetch_customer_internet_services,
            fetch_customers,
            map_customer_to_subscriber_data,
        )

        def _call_splynx(read_fn, *args):
            """Use a short-lived session so live Splynx reads do not pin the caller's DB connection."""
            splynx_db = SessionLocal()
            try:
                return read_fn(splynx_db, *args)
            finally:
                splynx_db.close()

        customers = _call_splynx(fetch_customers)
        live_results: list[dict] = []
        today = datetime.now(UTC).date()
        customer_emails = {
            str(customer.get("email") or "").strip().lower()
            for customer in customers
            if isinstance(customer, Mapping) and str(customer.get("email") or "").strip()
        }
        customer_external_ids = {
            str(customer.get("id") or "").strip()
            for customer in customers
            if isinstance(customer, Mapping) and str(customer.get("id") or "").strip()
        }
        customer_logins = {
            str(customer.get("login") or "").strip()
            for customer in customers
            if isinstance(customer, Mapping) and str(customer.get("login") or "").strip()
        }
        people_by_email: dict[str, tuple[str, str]] = {}
        channels_by_person: dict[str, list[PersonChannel]] = {}
        subscriber_sync_by_external_id: dict[str, tuple[Mapping[str, Any], datetime | None]] = {}
        subscriber_sync_by_email: dict[str, tuple[Mapping[str, Any], datetime | None]] = {}
        subscriber_sync_by_login: dict[str, tuple[Mapping[str, Any], datetime | None]] = {}
        if customer_emails:
            matched_people = db.execute(
                select(Person.id, Person.email, Person.phone).where(func.lower(Person.email).in_(customer_emails))
            ).all()
            people_by_email = {
                str(email).strip().lower(): (str(person_id), str(phone or "").strip())
                for person_id, email, phone in matched_people
                if email
            }
            person_ids = [person_id for person_id, _email, _phone in matched_people]
            if person_ids:
                channel_rows = db.execute(
                    select(PersonChannel).where(
                        PersonChannel.person_id.in_(person_ids),
                        PersonChannel.channel_type.in_(
                            [PersonChannelType.phone, PersonChannelType.whatsapp, PersonChannelType.sms]
                        ),
                    )
                ).scalars()
                for channel in channel_rows:
                    key = str(channel.person_id)
                    channels_by_person.setdefault(key, []).append(channel)

        if customer_external_ids or customer_emails or customer_logins:
            subscriber_rows = db.execute(
                select(
                    Subscriber.external_id,
                    Subscriber.subscriber_number,
                    Subscriber.sync_metadata,
                    Subscriber.suspended_at,
                    Person.email,
                )
                .select_from(Subscriber)
                .outerjoin(Person, Person.id == Subscriber.person_id)
                .where(
                    or_(
                        Subscriber.external_id.in_(customer_external_ids) if customer_external_ids else false(),
                        Subscriber.subscriber_number.in_(customer_logins) if customer_logins else false(),
                        func.lower(Person.email).in_(customer_emails) if customer_emails else false(),
                    )
                )
            ).all()
            for external_id, subscriber_number, sync_metadata, suspended_at, person_email in subscriber_rows:
                cached_tuple = (
                    sync_metadata if isinstance(sync_metadata, Mapping) else {},
                    _coerce_datetime_utc(suspended_at),
                )
                external_key = str(external_id or "").strip()
                if external_key:
                    subscriber_sync_by_external_id[external_key] = cached_tuple
                login_key = str(subscriber_number or "").strip()
                if login_key:
                    subscriber_sync_by_login[login_key] = cached_tuple
                email_key = str(person_email or "").strip().lower()
                if email_key:
                    subscriber_sync_by_email[email_key] = cached_tuple

        def _contact_phone(email_value: str, default_phone: str) -> str:
            email_key = email_value.strip().lower()
            if not email_key:
                return default_phone
            person_match = people_by_email.get(email_key)
            if not person_match:
                return default_phone
            person_id, person_phone = person_match
            channels = channels_by_person.get(person_id, [])
            for preferred_type in [PersonChannelType.phone, PersonChannelType.whatsapp, PersonChannelType.sms]:
                primary = next(
                    (
                        channel.address.strip()
                        for channel in channels
                        if channel.channel_type == preferred_type and channel.is_primary and channel.address
                    ),
                    "",
                )
                if primary:
                    return primary
            for preferred_type in [PersonChannelType.phone, PersonChannelType.whatsapp, PersonChannelType.sms]:
                any_channel = next(
                    (
                        channel.address.strip()
                        for channel in channels
                        if channel.channel_type == preferred_type and channel.address
                    ),
                    "",
                )
                if any_channel:
                    return any_channel
            return person_phone or default_phone

        def _live_billing_start_date(
            customer_payload: Mapping[str, Any],
            mapped_payload: Mapping[str, Any],
        ) -> str:
            mapped_start = _coerce_datetime_utc(mapped_payload.get("activated_at"))
            if mapped_start is not None:
                return mapped_start.strftime("%Y-%m-%d")

            def _from_candidates(payload: Mapping[str, Any] | None) -> str:
                if not isinstance(payload, Mapping):
                    return ""
                for candidate in (
                    payload.get("start_date"),
                    payload.get("date_add"),
                    payload.get("conversion_date"),
                    payload.get("created_at"),
                    payload.get("created"),
                    payload.get("registration_date"),
                ):
                    parsed_date = _parse_iso_date_text(str(candidate or ""))
                    if parsed_date is not None:
                        parsed_dt = _coerce_datetime_utc(parsed_date)
                        if parsed_dt is not None:
                            return parsed_dt.strftime("%Y-%m-%d")
                return ""

            direct_date = _from_candidates(customer_payload)
            if direct_date:
                return direct_date

            return ""

        def _live_area_from_customer(customer_payload: Mapping[str, Any]) -> str:
            def _normalize_area(raw_value: object) -> str:
                text = str(raw_value or "").strip()
                if not text:
                    return ""
                area_value = re.sub(r"\s*\([^)]*\)", "", text).strip()
                area_value = re.sub(r"\s+access\b", "", area_value, flags=re.IGNORECASE).strip()
                area_value = re.sub(r"\s+", " ", area_value).strip(" -")
                return area_value

            def _extract_area(payload: Mapping[str, Any] | None) -> str:
                if not isinstance(payload, Mapping):
                    return ""
                for key in ("nas_name", "nas", "router_name", "router", "access_router", "access_name"):
                    area_value = _normalize_area(payload.get(key))
                    if area_value:
                        return area_value
                for key, value in payload.items():
                    key_text = str(key or "").strip().lower()
                    value_text = str(value or "").strip()
                    if not value_text:
                        continue
                    if any(token in key_text for token in ("nas", "router", "station", "access", "pop", "olt")):
                        area_value = _normalize_area(value)
                        if area_value:
                            return area_value
                    if re.search(r"\baccess\b", value_text, flags=re.IGNORECASE):
                        area_value = _normalize_area(value)
                        if area_value:
                            return area_value
                return ""

            direct_area = _extract_area(customer_payload)
            if direct_area:
                return direct_area
            additional_attributes = customer_payload.get("additional_attributes")
            if isinstance(additional_attributes, Mapping):
                return _extract_area(additional_attributes)
            if isinstance(additional_attributes, list):
                for item in additional_attributes:
                    if isinstance(item, Mapping):
                        direct_area = _extract_area(item)
                        if direct_area:
                            return direct_area
            return ""

        def _live_blocked_date(customer_payload: Mapping[str, Any], mapped_payload: Mapping[str, Any]) -> str:
            direct_suspended = _coerce_datetime_utc(mapped_payload.get("suspended_at"))
            if direct_suspended is not None:
                return direct_suspended.strftime("%Y-%m-%d")
            for candidate in (
                customer_payload.get("blocking_date"),
                customer_payload.get("blocked_date"),
                customer_payload.get("suspended_at"),
            ):
                parsed_date = _parse_iso_date_text(str(candidate or ""))
                if parsed_date is not None:
                    return parsed_date.strftime("%Y-%m-%d")

            external_key = str(customer_payload.get("id") or "").strip()
            if external_key:
                cached_match = subscriber_sync_by_external_id.get(external_key)
                if cached_match is not None:
                    _cached_sync, cached_suspended_at = cached_match
                    if cached_suspended_at is not None:
                        return cached_suspended_at.strftime("%Y-%m-%d")

            login_key = str(customer_payload.get("login") or "").strip()
            if login_key:
                cached_match = subscriber_sync_by_login.get(login_key)
                if cached_match is not None:
                    _cached_sync, cached_suspended_at = cached_match
                    if cached_suspended_at is not None:
                        return cached_suspended_at.strftime("%Y-%m-%d")

            email_key = str(customer_payload.get("email") or "").strip().lower()
            if email_key:
                cached_match = subscriber_sync_by_email.get(email_key)
                if cached_match is not None:
                    _cached_sync, cached_suspended_at = cached_match
                    if cached_suspended_at is not None:
                        return cached_suspended_at.strftime("%Y-%m-%d")
            return ""

        def _live_cached_sync_metadata(customer_payload: Mapping[str, Any]) -> Mapping[str, Any]:
            external_key = str(customer_payload.get("id") or "").strip()
            if external_key:
                cached_match = subscriber_sync_by_external_id.get(external_key)
                if cached_match is not None:
                    cached_sync, _cached_suspended_at = cached_match
                    return cached_sync

            login_key = str(customer_payload.get("login") or "").strip()
            if login_key:
                cached_match = subscriber_sync_by_login.get(login_key)
                if cached_match is not None:
                    cached_sync, _cached_suspended_at = cached_match
                    return cached_sync

            email_key = str(customer_payload.get("email") or "").strip().lower()
            if email_key:
                cached_match = subscriber_sync_by_email.get(email_key)
                if cached_match is not None:
                    cached_sync, _cached_suspended_at = cached_match
                    return cached_sync
            return {}

        def _enrich_visible_live_entry(entry: dict[str, Any]) -> dict[str, Any]:
            external_id = str(entry.get("_external_id") or "").strip()
            if not external_id:
                return {}
            if all(
                [
                    str(entry.get("billing_start_date") or "").strip(),
                    str(entry.get("invoiced_until") or "").strip(),
                    str(entry.get("next_bill_date") or "").strip(),
                    entry.get("days_to_due") is not None,
                    entry.get("days_past_due") is not None,
                ]
            ):
                return {}

            try:
                services_payload = _call_splynx(fetch_customer_internet_services, external_id)
            except Exception:
                services_payload = []
            try:
                billing_payload = _call_splynx(fetch_customer_billing, external_id)
            except Exception:
                billing_payload = {}
            detailed_customer = {
                "id": external_id,
                "internet_services": services_payload if isinstance(services_payload, list) else [],
                "billing": billing_payload if isinstance(billing_payload, Mapping) else {},
            }
            try:
                detailed_mapped = map_customer_to_subscriber_data(db, detailed_customer, include_remote_details=False)
            except Exception:
                return {}
            detailed_sync_metadata = (
                detailed_mapped.get("sync_metadata")
                if isinstance(detailed_mapped.get("sync_metadata"), Mapping)
                else {}
            )
            billing_start_date = _live_billing_start_date(detailed_customer, detailed_mapped)
            invoiced_until_text = _metadata_text(detailed_sync_metadata, "invoiced_until")
            if not invoiced_until_text:
                invoiced_until_text = (
                    _metadata_text(_live_cached_sync_metadata(detailed_customer), "invoiced_until")
                    or billing_start_date
                )
            invoiced_until_date = _parse_iso_date_text(invoiced_until_text)
            next_bill_raw = _coerce_datetime_utc(detailed_mapped.get("next_bill_date"))

            return {
                "billing_start_date": billing_start_date or str(entry.get("billing_start_date") or ""),
                "invoiced_until": invoiced_until_text or str(entry.get("invoiced_until") or ""),
                "next_bill_date": next_bill_raw.strftime("%Y-%m-%d")
                if next_bill_raw
                else str(entry.get("next_bill_date") or ""),
                "days_to_due": (
                    (next_bill_raw.date() - today).days if next_bill_raw is not None else entry.get("days_to_due")
                ),
                "days_past_due": (
                    max(0, (today - invoiced_until_date).days)
                    if invoiced_until_date is not None
                    else entry.get("days_past_due")
                ),
                "days_since_last_payment": (
                    max(0, (today - invoiced_until_date).days)
                    if invoiced_until_date is not None
                    else entry.get("days_since_last_payment")
                ),
            }

        for customer in customers:
            if not isinstance(customer, Mapping):
                continue
            mapped = map_customer_to_subscriber_data(db, dict(customer), include_remote_details=False)
            status_value = str(mapped.get("status") or "unknown")
            billing_start_date = _live_billing_start_date(customer, mapped)
            area_value = _live_area_from_customer(customer)
            next_bill_raw = _coerce_datetime_utc(mapped.get("next_bill_date"))
            due_days = (next_bill_raw.date() - today).days if next_bill_raw is not None else None
            balance_amount = _parse_balance_amount(mapped.get("balance") or customer.get("balance"))
            sync_metadata = mapped.get("sync_metadata") if isinstance(mapped.get("sync_metadata"), Mapping) else {}
            invoiced_until_text = _metadata_text(sync_metadata, "invoiced_until")
            if not invoiced_until_text:
                invoiced_until_text = (
                    _metadata_text(_live_cached_sync_metadata(customer), "invoiced_until") or billing_start_date
                )
            invoiced_until_date = _parse_iso_date_text(invoiced_until_text)
            days_since_last_payment = (
                max(0, (today - invoiced_until_date).days) if invoiced_until_date is not None else None
            )
            row_days_past_due = days_since_last_payment
            blocked_date_text = _live_blocked_date(customer, mapped)
            live_segment_value: str | None = None
            if status_value == SubscriberStatus.terminated.value:
                live_segment_value = "Churned"
            elif status_value == SubscriberStatus.suspended.value:
                live_segment_value = "Suspended"
            elif status_value == SubscriberStatus.pending.value:
                live_segment_value = "Pending"
            elif status_value == SubscriberStatus.active.value and due_days is not None and due_days < 0:
                live_segment_value = "Overdue"
            elif status_value == SubscriberStatus.active.value and due_days is not None and due_days <= due_soon_days:
                live_segment_value = "Due Soon"
            if live_segment_value is None:
                continue
            if selected_segments and live_segment_value not in selected_segments:
                continue
            if not _matches_days_past_due_bucket(row_days_past_due):
                continue

            display_name = str(customer.get("name") or "").strip() or str(mapped.get("subscriber_number") or "").strip()
            email_value = str(customer.get("email") or "").strip()
            phone_value = str(customer.get("phone") or "").strip()
            live_results.append(
                {
                    "subscriber_id": str(customer.get("id") or ""),
                    "name": _clean_report_name(display_name or "Unknown"),
                    "email": email_value,
                    "phone": _contact_phone(email_value, phone_value),
                    "subscriber_status": status_value.replace("_", " ").title(),
                    "area": area_value,
                    "billing_start_date": billing_start_date,
                    "next_bill_date": next_bill_raw.strftime("%Y-%m-%d") if next_bill_raw else "",
                    "balance": balance_amount,
                    "billing_cycle": str(mapped.get("billing_cycle") or ""),
                    "blocked_date": blocked_date_text,
                    "last_transaction_date": _metadata_text(sync_metadata, "last_transaction_date"),
                    "expires_in": _metadata_text(sync_metadata, "expires_in"),
                    "invoiced_until": invoiced_until_text,
                    "days_since_last_payment": days_since_last_payment,
                    "days_past_due": row_days_past_due,
                    "total_paid": _parse_balance_amount(_metadata_text(sync_metadata, "total_paid")),
                    "days_to_due": due_days,
                    "risk_segment": live_segment_value,
                    "_person_id": "",
                    "_external_id": str(customer.get("id") or ""),
                    "_subscriber_number": str(mapped.get("subscriber_number") or ""),
                    "_last_synced_at": "",
                }
            )

        live_results = _dedupe_churn_rows(live_results)
        avg_balance = round(sum(r["balance"] for r in live_results) / len(live_results), 2) if live_results else 0.0
        for entry in live_results:
            entry["is_high_balance_risk"] = entry["balance"] > avg_balance and entry["risk_segment"] in {
                "Overdue",
                "Suspended",
                "Churned",
            }
        if high_balance_only:
            live_results = [row for row in live_results if row["is_high_balance_risk"]]
        live_results.sort(
            key=lambda row: (
                -int(bool(row["is_high_balance_risk"])),
                -float(row["balance"]),
                (row["days_to_due"] if isinstance(row["days_to_due"], int) else 10**9),
                row["name"],
            )
        )
        visible_results = live_results[: max(1, int(limit))]
        if enrich_visible_rows and visible_results:
            from concurrent.futures import ThreadPoolExecutor

            max_workers = min(8, len(visible_results))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_enrich_visible_live_entry, entry) for entry in visible_results]
                for entry, future in zip(visible_results, futures, strict=False):
                    entry.update(future.result())
        return visible_results

    days_to_due = case(
        (Subscriber.next_bill_date.is_(None), None),
        else_=-_days_since_expr(db, Subscriber.next_bill_date),
    ).label("days_to_due")
    days_since_due = _days_since_expr(db, Subscriber.next_bill_date)

    active_at_risk_filter = and_(
        Subscriber.status == SubscriberStatus.active,
        Subscriber.next_bill_date.isnot(None),
        days_since_due >= -max(0, int(due_soon_days)),
    )
    row_scope_filter = or_(
        Subscriber.status.in_(
            [SubscriberStatus.terminated, SubscriberStatus.suspended, SubscriberStatus.pending],
        ),
        active_at_risk_filter,
    )
    segment_scope_filters = []
    for selected_segment in selected_segments:
        if selected_segment == "Churned":
            segment_scope_filters.append(Subscriber.status == SubscriberStatus.terminated)
        elif selected_segment == "Suspended":
            segment_scope_filters.append(Subscriber.status == SubscriberStatus.suspended)
        elif selected_segment == "Pending":
            segment_scope_filters.append(Subscriber.status == SubscriberStatus.pending)
        elif selected_segment == "Overdue":
            segment_scope_filters.append(
                and_(
                    Subscriber.status == SubscriberStatus.active,
                    Subscriber.next_bill_date.isnot(None),
                    days_since_due > 0,
                )
            )
        elif selected_segment == "Due Soon":
            segment_scope_filters.append(
                and_(
                    Subscriber.status == SubscriberStatus.active,
                    Subscriber.next_bill_date.isnot(None),
                    days_since_due <= 0,
                    days_since_due >= -max(0, int(due_soon_days)),
                )
            )
    segment_scope_filter = or_(*segment_scope_filters) if segment_scope_filters else None

    normalized_limit = max(1, int(limit))
    # We fetch a bounded superset because post-processing performs dedupe, scoring, and optional high-balance filtering.
    fetch_limit = min(max(normalized_limit * 20, 500), 20_000)

    rows = db.execute(
        select(
            Subscriber.id.label("subscriber_id"),
            Subscriber.subscriber_number,
            Subscriber.external_id,
            Subscriber.person_id,
            Subscriber.status,
            Subscriber.next_bill_date,
            Subscriber.balance,
            Subscriber.billing_cycle,
            Subscriber.sync_metadata,
            Subscriber.suspended_at,
            Subscriber.last_synced_at,
            Person.display_name,
            Person.first_name,
            Person.last_name,
            Person.email,
            Person.phone,
            days_to_due,
        )
        .select_from(Subscriber)
        .outerjoin(Person, Person.id == Subscriber.person_id)
        .where(Subscriber.is_active.is_(True), row_scope_filter)
        .where(segment_scope_filter if segment_scope_filter is not None else true())
        .limit(fetch_limit)
    ).all()

    results: list[dict] = []
    today = datetime.now(UTC).date()
    for row in rows:
        candidate_display = row.display_name or ""
        candidate_full = f"{row.first_name or ''} {row.last_name or ''}".strip()
        raw_name = candidate_display if not _looks_like_noise_name(candidate_display) else ""
        if not raw_name and not _looks_like_noise_name(candidate_full):
            raw_name = candidate_full
        if not raw_name:
            raw_name = row.subscriber_number or str(row.subscriber_id)
        status_value = row.status.value if row.status else "unknown"
        balance_amount = _parse_balance_amount(row.balance)
        due_days = row.days_to_due if isinstance(row.days_to_due, int) else None
        sync_metadata = row.sync_metadata if isinstance(row.sync_metadata, Mapping) else {}
        invoiced_until_text = _metadata_text(sync_metadata, "invoiced_until")
        invoiced_until_date = _parse_iso_date_text(invoiced_until_text)
        db_row_days_past_due: int | None = (
            max(0, (today - invoiced_until_date).days) if invoiced_until_date is not None else None
        )
        days_since_last_payment = db_row_days_past_due
        segment_value: str | None = None
        if status_value == SubscriberStatus.terminated.value:
            segment_value = "Churned"
        elif status_value == SubscriberStatus.suspended.value:
            segment_value = "Suspended"
        elif status_value == SubscriberStatus.pending.value:
            segment_value = "Pending"
        elif status_value == SubscriberStatus.active.value and due_days is not None and due_days < 0:
            segment_value = "Overdue"
        elif status_value == SubscriberStatus.active.value and due_days is not None and due_days <= due_soon_days:
            segment_value = "Due Soon"

        # Exclude active/current subscribers from this report.
        if segment_value is None:
            continue
        if selected_segments and segment_value not in selected_segments:
            continue
        if not _matches_days_past_due_bucket(db_row_days_past_due):
            continue
        results.append(
            {
                "subscriber_id": str(row.subscriber_id),
                "name": _clean_report_name(raw_name),
                "email": row.email or "",
                "phone": row.phone or "",
                "subscriber_status": status_value.replace("_", " ").title(),
                "area": "",
                "billing_start_date": "",
                "next_bill_date": row.next_bill_date.strftime("%Y-%m-%d") if row.next_bill_date else "",
                "balance": balance_amount,
                "billing_cycle": row.billing_cycle or "",
                "blocked_date": row.suspended_at.strftime("%Y-%m-%d") if row.suspended_at else "",
                "last_transaction_date": _metadata_text(sync_metadata, "last_transaction_date"),
                "expires_in": _metadata_text(sync_metadata, "expires_in"),
                "invoiced_until": invoiced_until_text,
                "days_since_last_payment": days_since_last_payment,
                "days_past_due": db_row_days_past_due,
                "total_paid": _parse_balance_amount(_metadata_text(sync_metadata, "total_paid")),
                "days_to_due": due_days,
                "risk_segment": segment_value,
                "_person_id": str(row.person_id) if row.person_id else "",
                "_external_id": str(row.external_id or ""),
                "_subscriber_number": str(row.subscriber_number or ""),
                "_last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else "",
            }
        )

    results = _dedupe_churn_rows(results)

    avg_balance = round(sum(r["balance"] for r in results) / len(results), 2) if results else 0.0
    for entry in results:
        entry["is_high_balance_risk"] = entry["balance"] > avg_balance and entry["risk_segment"] in {
            "Overdue",
            "Suspended",
            "Churned",
        }

    if high_balance_only:
        results = [row for row in results if row["is_high_balance_risk"]]

    results.sort(
        key=lambda row: (
            -int(bool(row["is_high_balance_risk"])),
            -float(row["balance"]),
            (row["days_to_due"] if isinstance(row["days_to_due"], int) else 10**9),
            row["name"],
        )
    )
    return results[:normalized_limit]


def get_overdue_invoices_table(
    db: Session,
    *,
    min_days_past_due: int = 30,
    limit: int = 500,
) -> list[dict]:
    """
    Overdue receivables (sales orders) by customer.

    In this codebase, customer invoices are represented by `sales_orders`.
    """
    dialect_name = db.get_bind().dialect.name if db.get_bind() is not None else ""
    due_dt = func.coalesce(SalesOrder.payment_due_date, SalesOrder.created_at)
    days_past_due = _days_since_expr(db, due_dt).label("days_past_due")
    oldest_due_day_expr = func.date(due_dt) if dialect_name == "sqlite" else cast(due_dt, Date)

    rows = db.execute(
        select(
            SalesOrder.person_id,
            func.count(SalesOrder.id).label("overdue_invoices"),
            func.sum(SalesOrder.balance_due).label("total_balance_due"),
            func.max(days_past_due).label("max_days_past_due"),
            func.min(oldest_due_day_expr).label("oldest_due_day"),
            Person.display_name,
            Person.first_name,
            Person.last_name,
            Person.email,
        )
        .select_from(SalesOrder)
        .join(Person, Person.id == SalesOrder.person_id, isouter=True)
        .where(
            SalesOrder.is_active.is_(True),
            func.coalesce(SalesOrder.balance_due, 0) > 0,
            SalesOrder.payment_status != SalesOrderPaymentStatus.paid,
            days_past_due >= min_days_past_due,
        )
        .group_by(
            SalesOrder.person_id,
            Person.display_name,
            Person.first_name,
            Person.last_name,
            Person.email,
        )
        .order_by(func.max(days_past_due).desc(), func.sum(SalesOrder.balance_due).desc())
        .limit(limit)
    ).all()

    results: list[dict] = []
    for row in rows:
        raw_name = (
            row.display_name
            or f"{row.first_name or ''} {row.last_name or ''}".strip()
            or (row.email.split(",")[0].strip() if row.email else "")
            or "Unknown"
        )
        oldest_due_day = ""
        if row.oldest_due_day:
            if isinstance(row.oldest_due_day, str):
                oldest_due_day = row.oldest_due_day[:10]
            else:
                oldest_due_day = row.oldest_due_day.strftime("%Y-%m-%d")
        results.append(
            {
                "person_id": str(row.person_id) if row.person_id else "",
                "name": _clean_report_name(raw_name),
                "email": row.email or "",
                "overdue_invoices": int(row.overdue_invoices or 0),
                "total_balance_due": float(row.total_balance_due or 0),
                "max_days_past_due": int(row.max_days_past_due or 0),
                "oldest_due_day": oldest_due_day,
            }
        )

    return results


_CHURN_SEGMENT_ORDER = ["Overdue", "Suspended", "Churned", "Pending", "Due Soon"]


def churn_risk_summary(
    churn_rows: list[dict],
    overdue_invoices: list[dict],
    recent_churn_kpis: dict[str, Any] | None = None,
) -> dict[str, float | int]:
    """Topline KPIs for the billing-risk churn dashboard."""
    recent_churn_kpis = recent_churn_kpis or {}
    total_at_risk = len(churn_rows)
    total_balance_exposure = round(sum(float(row.get("balance") or 0) for row in churn_rows), 2)
    high_balance_risk_count = sum(1 for row in churn_rows if bool(row.get("is_high_balance_risk")))
    overdue_count = sum(1 for row in churn_rows if row.get("risk_segment") == "Overdue")
    overdue_balance_exposure = round(
        sum(float(row.get("balance") or 0) for row in churn_rows if row.get("risk_segment") == "Overdue"),
        2,
    )
    overdue_invoice_balance = round(
        sum(float(row.get("total_balance_due") or 0) for row in overdue_invoices),
        2,
    )

    return {
        "total_at_risk": total_at_risk,
        "total_balance_exposure": total_balance_exposure,
        "high_balance_risk_count": high_balance_risk_count,
        "high_balance_risk_pct": round((high_balance_risk_count / total_at_risk) * 100, 1) if total_at_risk else 0,
        "overdue_count": overdue_count,
        "overdue_balance_exposure": overdue_balance_exposure,
        "overdue_invoice_balance": overdue_invoice_balance,
        "recent_churned_count": int(recent_churn_kpis.get("churned_count") or 0),
        "recent_churn_rate": float(recent_churn_kpis.get("churn_rate") or 0),
        "recent_revenue_lost": float(recent_churn_kpis.get("revenue_lost_to_churn") or 0),
    }


def churn_risk_segment_breakdown(churn_rows: list[dict]) -> list[dict[str, float | int | str]]:
    """Counts and exposure by risk segment."""
    segment_map: dict[str, dict[str, float | int | str]] = {}
    segment_billing_cycles: dict[str, dict[str, int]] = {}
    segment_payment_days: dict[str, list[int]] = {}
    total_count = len(churn_rows)

    for segment in _CHURN_SEGMENT_ORDER:
        segment_map[segment] = {
            "segment": segment,
            "count": 0,
            "balance": 0.0,
            "high_balance_count": 0,
            "avg_balance": 0.0,
            "share_pct": 0.0,
            "billing_mix": "",
        }
        segment_billing_cycles[segment] = {}
        segment_payment_days[segment] = []

    for row in churn_rows:
        segment = str(row.get("risk_segment") or "Unknown")
        if segment not in segment_map:
            segment_map[segment] = {
                "segment": segment,
                "count": 0,
                "balance": 0.0,
                "high_balance_count": 0,
                "avg_balance": 0.0,
                "share_pct": 0.0,
                "billing_mix": "",
            }
            segment_billing_cycles[segment] = {}
            segment_payment_days[segment] = []
        segment_map[segment]["count"] = int(segment_map[segment]["count"]) + 1
        segment_map[segment]["balance"] = round(
            float(segment_map[segment]["balance"]) + float(row.get("balance") or 0), 2
        )
        if row.get("is_high_balance_risk"):
            segment_map[segment]["high_balance_count"] = int(segment_map[segment]["high_balance_count"]) + 1
        billing_cycle = str(row.get("billing_cycle") or "").strip().lower()
        if billing_cycle:
            segment_cycles = segment_billing_cycles[segment]
            segment_cycles[billing_cycle] = segment_cycles.get(billing_cycle, 0) + 1
        days_since_last_payment = row.get("days_since_last_payment")
        if isinstance(days_since_last_payment, int):
            segment_payment_days[segment].append(days_since_last_payment)
        elif isinstance(days_since_last_payment, str) and days_since_last_payment.strip().isdigit():
            segment_payment_days[segment].append(int(days_since_last_payment.strip()))
        else:
            invoiced_until_date = _parse_iso_date_text(str(row.get("invoiced_until") or ""))
            if invoiced_until_date is not None:
                segment_payment_days[segment].append(max(0, (datetime.now(UTC).date() - invoiced_until_date).days))

    results = list(segment_map.values())
    for row in results:
        count = int(row["count"])
        balance = float(row["balance"])
        row["avg_balance"] = round(balance / count, 2) if count else 0.0
        row["share_pct"] = round((count / total_count) * 100, 1) if total_count else 0.0
        cycle_counts = segment_billing_cycles.get(str(row["segment"]), {})
        payment_days = segment_payment_days.get(str(row["segment"]), [])
        payment_recency_text = ""
        if payment_days:
            avg_days = round(sum(payment_days) / len(payment_days))
            payment_recency_text = f"Avg {avg_days}d since payment ({len(payment_days)} accounts)"
        if cycle_counts and payment_recency_text:
            sorted_cycles = sorted(cycle_counts.items(), key=lambda item: (-item[1], item[0]))
            cycle_mix_text = ", ".join(
                f"{cycle_name.replace('_', ' ').title()} ({cycle_count})"
                for cycle_name, cycle_count in sorted_cycles[:3]
            )
            row["billing_mix"] = f"{payment_recency_text} | {cycle_mix_text}"
        elif cycle_counts:
            sorted_cycles = sorted(cycle_counts.items(), key=lambda item: (-item[1], item[0]))
            row["billing_mix"] = ", ".join(
                f"{cycle_name.replace('_', ' ').title()} ({cycle_count})"
                for cycle_name, cycle_count in sorted_cycles[:3]
            )
        elif payment_recency_text:
            row["billing_mix"] = payment_recency_text
        else:
            row["billing_mix"] = "No billing cycle data"

    results.sort(
        key=lambda row: (
            _CHURN_SEGMENT_ORDER.index(str(row["segment"]))
            if str(row["segment"]) in _CHURN_SEGMENT_ORDER
            else len(_CHURN_SEGMENT_ORDER),
            -int(row["count"]),
        )
    )
    return [row for row in results if int(row["count"]) > 0]


def churn_risk_aging_buckets(churn_rows: list[dict], *, due_soon_days: int = 7) -> list[dict[str, int | str]]:
    """Bucket at-risk subscribers by due-date aging."""
    buckets = {
        f"Due In 0-{due_soon_days} Days": 0,
        "Overdue 1-7 Days": 0,
        "Overdue 8-30 Days": 0,
        "Overdue 31+ Days": 0,
        "No Due Date / Status Driven": 0,
    }

    for row in churn_rows:
        due_days = row.get("days_to_due")
        if not isinstance(due_days, int):
            buckets["No Due Date / Status Driven"] += 1
            continue
        if due_days < 0:
            overdue_days = abs(due_days)
            if overdue_days <= 7:
                buckets["Overdue 1-7 Days"] += 1
            elif overdue_days <= 30:
                buckets["Overdue 8-30 Days"] += 1
            else:
                buckets["Overdue 31+ Days"] += 1
        elif due_days <= due_soon_days:
            buckets[f"Due In 0-{due_soon_days} Days"] += 1
        else:
            buckets["No Due Date / Status Driven"] += 1

    return [{"label": label, "count": count} for label, count in buckets.items()]


def churned_subscribers_kpis(
    db: Session,
    start_dt: datetime,
    end_dt: datetime,
    *,
    behavioral_days: int = 60,
) -> dict:
    """Summary metrics for unified churned subscribers in a date range."""
    resolved = _unified_churn_expressions(db, behavioral_days)
    activation_event_at = resolved["activation_event_at"]
    successful_payment_sq = resolved["successful_payment_sq"]
    churn_event_at = resolved["churn_date"]
    churn_type = resolved["churn_type"]
    churn_filters = (
        churn_event_at.isnot(None),
        churn_event_at >= start_dt,
        churn_event_at <= end_dt,
        activation_event_at <= churn_event_at,
    )

    churn_counts_row = db.execute(
        select(
            func.count(func.distinct(Subscriber.id)).label("churned_count"),
            func.count(
                func.distinct(
                    case(
                        (churn_type == "operational", Subscriber.id),
                        else_=None,
                    )
                )
            ).label("operational_count"),
            func.count(
                func.distinct(
                    case(
                        (churn_type == "behavioral", Subscriber.id),
                        else_=None,
                    )
                )
            ).label("behavioral_count"),
        )
        .select_from(Subscriber)
        .outerjoin(successful_payment_sq, successful_payment_sq.c.person_id == Subscriber.person_id)
        .where(*churn_filters)
    ).one()

    churned_rows = db.execute(
        select(
            Subscriber.id.label("subscriber_id"),
            Subscriber.person_id,
            Subscriber.service_plan,
            Subscriber.service_name,
            Subscriber.service_speed,
            Subscriber.service_region,
            activation_event_at.label("activation_event_at"),
            churn_event_at.label("churn_event_at"),
            churn_type.label("churn_type"),
            Person.first_name,
            Person.last_name,
            Person.display_name,
        )
        .outerjoin(successful_payment_sq, successful_payment_sq.c.person_id == Subscriber.person_id)
        .outerjoin(Person, Person.id == Subscriber.person_id)
        .where(*churn_filters)
    ).all()

    churned_count = int(churn_counts_row.churned_count or 0)
    operational_count = int(churn_counts_row.operational_count or 0)
    behavioral_count = int(churn_counts_row.behavioral_count or 0)
    tenure_days: list[int] = []
    plan_counts: dict[str, int] = defaultdict(int)
    impacted_regions: set[str] = set()
    revenue_lost_to_churn = 0.0
    churned_people: dict[Any, str] = {}
    fallback_names: list[str] = []
    seen_subscriber_ids: set[Any] = set()

    for row in churned_rows:
        if row.subscriber_id in seen_subscriber_ids:
            continue
        seen_subscriber_ids.add(row.subscriber_id)
        if row.activation_event_at and row.churn_event_at:
            tenure_days.append(max(0, (row.churn_event_at.date() - row.activation_event_at.date()).days))
        plan_name = (row.service_plan or "").strip() or (row.service_name or "").strip() or "Unknown"
        plan_counts[plan_name] += 1
        revenue_lost_to_churn += _estimate_monthly_plan_value(row.service_plan or row.service_name, row.service_speed)
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
            select(func.count(Subscriber.id))
            .where(
                activation_event_at <= start_dt,
                ((churn_event_at.is_(None)) | (churn_event_at > start_dt)),
            )
            .select_from(Subscriber)
            .outerjoin(successful_payment_sq, successful_payment_sq.c.person_id == Subscriber.person_id)
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
        "count_operational_churn": operational_count,
        "count_behavioral_churn": behavioral_count,
        "total_active_subscribers_start": active_at_start,
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


def churned_subscribers_trend(
    db: Session,
    start_dt: datetime,
    end_dt: datetime,
    *,
    behavioral_days: int = 60,
) -> list[dict]:
    """Daily unified churn count in the selected date range."""
    resolved = _unified_churn_expressions(db, behavioral_days)
    churn_event_at = resolved["churn_date"]
    activation_event_at = resolved["activation_event_at"]
    successful_payment_sq = resolved["successful_payment_sq"]
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
            activation_event_at <= churn_event_at,
        )
        .select_from(Subscriber)
        .outerjoin(successful_payment_sq, successful_payment_sq.c.person_id == Subscriber.person_id)
        .group_by("day")
        .order_by("day")
    ).all()
    return [{"date": str(row.day)[:10], "count": int(row._mapping["count"] or 0)} for row in rows if row.day]


def churned_subscribers_rows(
    db: Session,
    start_dt: datetime,
    end_dt: datetime,
    limit: int = 50,
    *,
    behavioral_days: int = 60,
) -> list[dict]:
    """Detailed unified churned subscribers in the selected date range."""
    resolved = _unified_churn_expressions(db, behavioral_days)
    activation_event_at = resolved["activation_event_at"]
    churn_event_at = resolved["churn_date"]
    churn_type = resolved["churn_type"]
    successful_payment_sq = resolved["successful_payment_sq"]
    subs = db.execute(
        select(
            Subscriber.subscriber_number,
            Subscriber.service_plan,
            Subscriber.service_region,
            activation_event_at.label("activation_event_at"),
            churn_event_at.label("churn_event_at"),
            churn_type.label("churn_type"),
            Person.first_name,
            Person.last_name,
            Person.display_name,
        )
        .outerjoin(successful_payment_sq, successful_payment_sq.c.person_id == Subscriber.person_id)
        .outerjoin(Person, Person.id == Subscriber.person_id)
        .where(
            churn_event_at.isnot(None),
            churn_event_at >= start_dt,
            churn_event_at <= end_dt,
            activation_event_at <= churn_event_at,
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
                "churn_type": row.churn_type or "",
                "tenure_days": tenure,
            }
        )
    return results


def churned_failed_payment_rows(
    db: Session,
    start_dt: datetime,
    end_dt: datetime,
    limit: int = 50,
    *,
    behavioral_days: int = 60,
) -> list[dict]:
    """Behavioral churn in period with outstanding failed payment signals."""
    resolved = _unified_churn_expressions(db, behavioral_days)
    activation_event_at = resolved["activation_event_at"]
    churn_event_at = resolved["churn_date"]
    churn_type = resolved["churn_type"]
    successful_payment_sq = resolved["successful_payment_sq"]
    rows = db.execute(
        select(
            Subscriber.subscriber_number,
            Subscriber.service_plan,
            Subscriber.service_name,
            Subscriber.balance.label("subscriber_balance"),
            Subscriber.next_bill_date.label("subscriber_due_date"),
            activation_event_at.label("activation_event_at"),
            churn_event_at.label("churn_event_at"),
            churn_type.label("churn_type"),
            Person.first_name,
            Person.last_name,
            Person.display_name,
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                SalesOrder.is_active.is_(True),
                                SalesOrder.payment_status.in_(
                                    [SalesOrderPaymentStatus.pending, SalesOrderPaymentStatus.partial]
                                ),
                                SalesOrder.balance_due > 0,
                            ),
                            SalesOrder.amount_paid,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("total_paid"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                SalesOrder.is_active.is_(True),
                                SalesOrder.payment_status.in_(
                                    [SalesOrderPaymentStatus.pending, SalesOrderPaymentStatus.partial]
                                ),
                                SalesOrder.balance_due > 0,
                            ),
                            SalesOrder.balance_due,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("outstanding_balance"),
            func.max(
                case(
                    (
                        and_(
                            SalesOrder.is_active.is_(True),
                            SalesOrder.payment_status.in_(
                                [SalesOrderPaymentStatus.pending, SalesOrderPaymentStatus.partial]
                            ),
                            SalesOrder.balance_due > 0,
                        ),
                        SalesOrder.payment_due_date,
                    ),
                    else_=None,
                )
            ).label("latest_due_date"),
            func.max(
                case(
                    (
                        and_(
                            SalesOrder.is_active.is_(True),
                            SalesOrder.payment_status.in_(
                                [SalesOrderPaymentStatus.pending, SalesOrderPaymentStatus.partial]
                            ),
                            SalesOrder.balance_due > 0,
                        ),
                        SalesOrder.updated_at,
                    ),
                    else_=None,
                )
            ).label("latest_payment_update"),
        )
        .outerjoin(successful_payment_sq, successful_payment_sq.c.person_id == Subscriber.person_id)
        .outerjoin(Person, Person.id == Subscriber.person_id)
        .outerjoin(
            SalesOrder,
            SalesOrder.person_id == Subscriber.person_id,
        )
        .where(
            churn_event_at.isnot(None),
            churn_event_at >= start_dt,
            churn_event_at <= end_dt,
            activation_event_at <= churn_event_at,
            churn_type == "behavioral",
        )
        .group_by(
            Subscriber.subscriber_number,
            Subscriber.service_plan,
            Subscriber.service_name,
            Subscriber.balance,
            Subscriber.next_bill_date,
            activation_event_at,
            churn_event_at,
            churn_type,
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
        outstanding_balance = round(float(row.outstanding_balance or 0), 2)
        if outstanding_balance <= 0:
            outstanding_balance = _parse_balance_amount(row.subscriber_balance)

        due_date_value = row.latest_due_date or row.subscriber_due_date
        results.append(
            {
                "name": name,
                "subscriber_number": row.subscriber_number or "",
                "plan": row.service_plan or row.service_name or "",
                "churn_type": row.churn_type or "",
                "activated_at": row.activation_event_at.strftime("%Y-%m-%d") if row.activation_event_at else "",
                "terminated_at": row.churn_event_at.strftime("%Y-%m-%d") if row.churn_event_at else "",
                "total_paid": round(float(row.total_paid or 0), 2),
                "outstanding_balance": outstanding_balance,
                "due_date": due_date_value.strftime("%Y-%m-%d") if due_date_value else "",
                "payment_updated_at": row.latest_payment_update.strftime("%Y-%m-%d")
                if row.latest_payment_update
                else "",
            }
        )
    results.sort(key=lambda item: (-float(item["outstanding_balance"]), item["name"]))
    if limit > 0:
        results = results[:limit]
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
        activation_date = _date_value(row.activation_event_at)
        tenure = (now.date() - activation_date).days if activation_date else 0
        results.append(
            {
                "name": name,
                "subscriber_number": row.subscriber_number,
                "plan": row.service_plan or "",
                "region": row.service_region or "",
                "activated_at": _format_date_value(row.activation_event_at),
                "tenure_days": tenure,
                "total_paid": round(float(row.total_paid or 0), 2),
            }
        )
    return results


def lifecycle_top_subscribers_by_value(db: Session, limit: int = 10) -> list[dict]:
    """Top subscribers of all time by realized paid amount on sales orders."""
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    sales_totals = (
        select(
            SalesOrder.person_id.label("person_id"),
            func.coalesce(func.sum(SalesOrder.amount_paid), 0).label("total_paid"),
            func.count(SalesOrder.id).label("order_count"),
        )
        .where(
            SalesOrder.is_active.is_(True),
            SalesOrder.status.in_([SalesOrderStatus.confirmed, SalesOrderStatus.paid, SalesOrderStatus.fulfilled]),
        )
        .group_by(SalesOrder.person_id)
        .subquery()
    )
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
            sales_totals.c.total_paid,
            sales_totals.c.order_count,
        )
        .join(Person, Person.id == Subscriber.person_id)
        .join(sales_totals, sales_totals.c.person_id == Subscriber.person_id)
        .order_by(sales_totals.c.total_paid.desc(), sales_totals.c.order_count.desc(), activation_event_at.desc())
    ).all()

    now = datetime.now(UTC)
    deduped: dict[Any, dict[str, Any]] = {}
    for row in rows:
        raw_name = row.display_name or f"{row.first_name or ''} {row.last_name or ''}".strip() or row.subscriber_number
        clean_name = _clean_report_name(raw_name)
        total_paid = float(row.total_paid or 0)
        activation_at = _coerce_datetime_utc(row.activation_event_at)
        activation_date = _date_value(activation_at)
        tenure_days = (now.date() - activation_date).days if activation_date else 0
        tenure_months = round(tenure_days / 30.4, 1) if tenure_days > 0 else 0
        avg_monthly_spend = round(total_paid / tenure_months, 2) if tenure_months > 0 else total_paid
        status = row.status.value if row.status else "unknown"
        candidate_subscriber_number = row.subscriber_number or ""
        result_row = {
            "subscriber_id": str(row.person_id) if row.person_id else (candidate_subscriber_number or clean_name),
            "name": clean_name,
            "subscriber_number": candidate_subscriber_number,
            "plan": row.service_plan or "",
            "status": status,
            "activated_at": _format_date_value(activation_at),
            "tenure_months": tenure_months,
            "order_count": int(row.order_count or 0),
            "total_paid": round(total_paid, 2),
            "avg_monthly_spend": avg_monthly_spend,
        }

        existing = deduped.get(row.person_id)
        if existing is None:
            deduped[row.person_id] = result_row
            continue

        existing_subscriber_number = existing["subscriber_number"] or ""
        candidate_digit_count = sum(ch.isdigit() for ch in candidate_subscriber_number)
        existing_digit_count = sum(ch.isdigit() for ch in existing_subscriber_number)
        if candidate_digit_count > existing_digit_count or (
            candidate_digit_count == existing_digit_count
            and len(candidate_subscriber_number) > len(existing_subscriber_number)
        ):
            existing["subscriber_number"] = candidate_subscriber_number
        if not existing.get("plan") and result_row["plan"]:
            existing["plan"] = result_row["plan"]
        if existing["name"].strip().lower() == (existing["subscriber_number"] or "").strip().lower() and clean_name:
            existing["name"] = clean_name

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


def _sync_metadata_text_expr(db: Session, key: str):
    """Cross-dialect access to sync_metadata string values."""
    dialect_name = db.get_bind().dialect.name if db.get_bind() is not None else ""
    if dialect_name == "sqlite":
        return func.json_extract(Subscriber.sync_metadata, f"$.{key}")
    return Subscriber.sync_metadata.op("->>")(key)


def _sync_metadata_date_expr(db: Session, key: str):
    """Cross-dialect safe date expression for sync_metadata date-like values."""
    dialect_name = db.get_bind().dialect.name if db.get_bind() is not None else ""
    text_expr = _sync_metadata_text_expr(db, key)
    if dialect_name == "sqlite":
        return func.date(text_expr)
    return cast(func.nullif(text_expr, ""), Date)


def _postgres_interval_days(day_count: int) -> Any:
    """Build a PostgreSQL interval expression for integer day offsets."""

    safe_day_count = max(1, int(day_count))
    return text(f"({safe_day_count} * interval '1 day')")


def _behavioral_churn_event_at(db: Session):
    """Derived churn date from 40+ day non-payment / overdue due-date signals."""
    dialect_name = db.get_bind().dialect.name if db.get_bind() is not None else ""
    last_payment_date = _sync_metadata_date_expr(db, "last_transaction_date")

    if dialect_name == "sqlite":
        threshold_date = func.date("now", "-40 days")
        derived_from_last_payment = case(
            (
                and_(last_payment_date.isnot(None), last_payment_date <= threshold_date),
                func.datetime(last_payment_date, "+40 days"),
            ),
            else_=None,
        )
        invoice_due_date = func.date(Subscriber.next_bill_date)
        balance_positive = cast(func.replace(func.coalesce(Subscriber.balance, "0"), ",", ""), Numeric) > 0
        derived_from_invoice_due = case(
            (
                and_(invoice_due_date.isnot(None), invoice_due_date <= threshold_date, balance_positive),
                func.datetime(invoice_due_date, "+40 days"),
            ),
            else_=None,
        )
        return case(
            (
                and_(derived_from_last_payment.isnot(None), derived_from_invoice_due.isnot(None)),
                func.min(derived_from_last_payment, derived_from_invoice_due),
            ),
            else_=func.coalesce(derived_from_last_payment, derived_from_invoice_due),
        )

    threshold_interval: Any = _postgres_interval_days(40)
    threshold_date_pg: Any = cast(func.current_date() - threshold_interval, Date)
    derived_from_last_payment = case(
        (
            and_(last_payment_date.isnot(None), last_payment_date <= threshold_date_pg),
            cast(last_payment_date + threshold_interval, DateTime(timezone=True)),
        ),
        else_=None,
    )
    invoice_due_date_pg: Any = cast(Subscriber.next_bill_date, Date)
    clean_balance = func.nullif(
        func.regexp_replace(func.coalesce(Subscriber.balance, ""), r"[^0-9.\-]", "", "g"),
        "",
    )
    balance_positive = cast(clean_balance, Numeric) > 0
    derived_from_invoice_due = case(
        (
            and_(invoice_due_date_pg.isnot(None), invoice_due_date_pg <= threshold_date_pg, balance_positive),
            cast(invoice_due_date_pg + threshold_interval, DateTime(timezone=True)),
        ),
        else_=None,
    )
    return case(
        (
            and_(derived_from_last_payment.isnot(None), derived_from_invoice_due.isnot(None)),
            func.least(derived_from_last_payment, derived_from_invoice_due),
        ),
        else_=func.coalesce(derived_from_last_payment, derived_from_invoice_due),
    )


def _churn_event_at(db: Session):
    return func.coalesce(
        _strict_churn_event_at(),
        _behavioral_churn_event_at(db),
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


def _successful_payment_subquery():
    """Latest successful payment timestamp per person."""
    return (
        select(
            SalesOrder.person_id.label("person_id"),
            func.max(func.coalesce(SalesOrder.paid_at, SalesOrder.updated_at, SalesOrder.created_at)).label(
                "last_successful_payment_at"
            ),
        )
        .where(
            SalesOrder.is_active.is_(True),
            SalesOrder.status.in_([SalesOrderStatus.confirmed, SalesOrderStatus.paid, SalesOrderStatus.fulfilled]),
            or_(SalesOrder.payment_status == SalesOrderPaymentStatus.paid, SalesOrder.amount_paid > 0),
        )
        .group_by(SalesOrder.person_id)
        .subquery()
    )


def _unified_churn_expressions(db: Session, behavioral_days: int):
    """Unified churn expressions with operational priority and behavioral fallback."""
    dialect_name = db.get_bind().dialect.name if db.get_bind() is not None else ""
    threshold_days = max(1, int(behavioral_days))
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    successful_payment_sq = _successful_payment_subquery()
    last_successful_payment_at = successful_payment_sq.c.last_successful_payment_at
    last_transaction_at = cast(_sync_metadata_date_expr(db, "last_transaction_date"), DateTime(timezone=True))
    latest_payment_signal_at: Any
    behavioral_reference_date: Any
    behavioral_cutoff_date: Any
    invoice_due_date: Any
    if dialect_name == "sqlite":
        latest_payment_signal_at = case(
            (last_successful_payment_at.is_(None), last_transaction_at),
            (last_transaction_at.is_(None), last_successful_payment_at),
            (last_successful_payment_at >= last_transaction_at, last_successful_payment_at),
            else_=last_transaction_at,
        )
        behavioral_reference_date = func.date(latest_payment_signal_at)
        behavioral_cutoff_date = func.date("now", f"-{threshold_days} days")
        invoice_due_date = func.date(Subscriber.next_bill_date)
        balance_positive = cast(func.replace(func.coalesce(Subscriber.balance, "0"), ",", ""), Numeric) > 0
    else:
        threshold_interval: Any = _postgres_interval_days(threshold_days)
        latest_payment_signal_at = func.greatest(
            func.coalesce(last_successful_payment_at, cast("1970-01-01", DateTime(timezone=True))),
            func.coalesce(last_transaction_at, cast("1970-01-01", DateTime(timezone=True))),
        )
        latest_payment_signal_at = case(
            (
                and_(last_successful_payment_at.is_(None), last_transaction_at.is_(None)),
                None,
            ),
            else_=latest_payment_signal_at,
        )
        behavioral_reference_date = cast(latest_payment_signal_at, Date)
        behavioral_cutoff_date = cast(func.current_date() - threshold_interval, Date)
        invoice_due_date = cast(Subscriber.next_bill_date, Date)
        clean_balance = func.nullif(
            func.regexp_replace(func.coalesce(Subscriber.balance, ""), r"[^0-9.\-]", "", "g"),
            "",
        )
        balance_positive = cast(clean_balance, Numeric) > 0

    # Operational churn: explicit termination status/date.
    is_operational_status = or_(
        Subscriber.status == SubscriberStatus.terminated,
        cast(Subscriber.status, String).in_(["terminated", "cancelled", "inactive"]),
    )
    operational_churn_date = func.coalesce(
        Subscriber.terminated_at,
        case((is_operational_status, Subscriber.updated_at), else_=None),
    )

    if dialect_name == "sqlite":
        behavioral_from_payment = case(
            (
                and_(
                    operational_churn_date.is_(None),
                    Subscriber.status != SubscriberStatus.pending,
                    behavioral_reference_date.isnot(None),
                    behavioral_reference_date <= behavioral_cutoff_date,
                ),
                func.datetime(behavioral_reference_date, f"+{threshold_days} days"),
            ),
            else_=None,
        )
        behavioral_from_due = case(
            (
                and_(
                    operational_churn_date.is_(None),
                    Subscriber.status != SubscriberStatus.pending,
                    invoice_due_date.isnot(None),
                    invoice_due_date <= behavioral_cutoff_date,
                    balance_positive,
                ),
                func.datetime(invoice_due_date, f"+{threshold_days} days"),
            ),
            else_=None,
        )
        behavioral_churn_date = case(
            (
                and_(behavioral_from_payment.isnot(None), behavioral_from_due.isnot(None)),
                func.min(behavioral_from_payment, behavioral_from_due),
            ),
            else_=func.coalesce(behavioral_from_payment, behavioral_from_due),
        )
    else:
        behavioral_from_payment = case(
            (
                and_(
                    operational_churn_date.is_(None),
                    Subscriber.status != SubscriberStatus.pending,
                    behavioral_reference_date.isnot(None),
                    behavioral_reference_date <= behavioral_cutoff_date,
                ),
                cast(
                    behavioral_reference_date + threshold_interval,
                    DateTime(timezone=True),
                ),
            ),
            else_=None,
        )
        behavioral_from_due = case(
            (
                and_(
                    operational_churn_date.is_(None),
                    Subscriber.status != SubscriberStatus.pending,
                    invoice_due_date.isnot(None),
                    invoice_due_date <= behavioral_cutoff_date,
                    balance_positive,
                ),
                cast(
                    invoice_due_date + threshold_interval,
                    DateTime(timezone=True),
                ),
            ),
            else_=None,
        )
        behavioral_churn_date = case(
            (
                and_(behavioral_from_payment.isnot(None), behavioral_from_due.isnot(None)),
                func.least(behavioral_from_payment, behavioral_from_due),
            ),
            else_=func.coalesce(behavioral_from_payment, behavioral_from_due),
        )

    churn_date = func.coalesce(operational_churn_date, behavioral_churn_date)
    churn_type = case(
        (operational_churn_date.isnot(None), "operational"),
        (behavioral_churn_date.isnot(None), "behavioral"),
        else_=None,
    )
    return {
        "activation_event_at": activation_event_at,
        "successful_payment_sq": successful_payment_sq,
        "churn_date": churn_date,
        "churn_type": churn_type,
    }


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
