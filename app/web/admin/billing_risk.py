"""Dedicated Billing Risk admin routes."""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.csrf import get_csrf_token
from app.db import end_read_only_transaction, get_db
from app.models.crm.team import CrmAgent, CrmAgentTeam, CrmTeam
from app.models.customer_retention import CustomerRetentionEngagement
from app.models.person import Person
from app.models.service_team import ServiceTeam, ServiceTeamMember
from app.models.subscriber import Subscriber
from app.services import billing_risk_cache
from app.services import billing_risk_reports as billing_risk_service
from app.services.common import coerce_uuid
from app.services.crm.web_campaigns import create_billing_risk_outreach_campaign, outreach_channel_target_options
from app.tasks.subscribers import sync_subscribers_from_splynx
from app.web.admin._auth_helpers import get_current_user, get_sidebar_stats
from app.web.auth.rbac import require_web_role
from app.web.templates import Jinja2Templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["admin-reports"])
customer_retention_router = APIRouter(tags=["admin-customer-retention"])
templates = Jinja2Templates(directory="templates")

RETENTION_PIPELINE_STEPS = ("Contacted", "Follow-up Pending", "Promised to Pay", "Resolved", "Lost")
ENTERPRISE_MRR_THRESHOLD = int(billing_risk_service.ENTERPRISE_MRR_THRESHOLD)
RETENTION_FIXED_REP_NAMES = (
    "Chizaram Ogbonna",
    "Grace Moses",
    "Abigail Tongov",
    "Stephanie Mojekwu",
)
RETENTION_REP_TEAM_OVERRIDES = {
    "ahmed omodara": "Customer Support",
    "chinenye onyeagba": "Customer Support",
    "chris mbah": "Customer Support",
    "david dikko": "Customer Support",
    "david ekechukwu": "Customer Support",
    "divine madu": "Customer Support",
    "james akah": "Customer Support",
    "monica eyire edako": "Customer Support",
    "seun ayoade": "Customer Support",
    "shallom chukwueke": "Customer Support",
    "chinelo okoro": "Sales Call Center",
    "martin nwaogu": "Sales Call Center",
    "ochanya abah": "Sales Call Center",
    "ruth ogbedebi": "Sales Call Center",
}
RETENTION_REP_LABEL_ONLY_NAMES = {"ejiro onovwiona"}
RETENTION_EXCLUDED_CUSTOMER_NAMES = {"test", "test account"}


def _normalize_customer_name_for_exclusion(name: object) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(name or "").strip().casefold())
    return re.sub(r"\s+", " ", normalized).strip()


def _is_excluded_retention_customer(name: object) -> bool:
    normalized = _normalize_customer_name_for_exclusion(name)
    return bool(normalized) and (
        normalized in RETENTION_EXCLUDED_CUSTOMER_NAMES or normalized.startswith("test account")
    )


def _normalize_segment_filters(segments: list[str] | str | None, segment: str | None) -> list[str]:
    raw_values: list[str] = []
    if isinstance(segments, list):
        raw_values.extend(segments)
    elif isinstance(segments, str):
        raw_values.append(segments)
    if segment:
        raw_values.append(segment)

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        for part in str(raw_value).split(","):
            candidate = part.strip().lower().replace(" ", "_")
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
    return normalized


def _segment_labels(selected_segments: list[str]) -> set[str]:
    mapping = {
        "overdue": "Due Soon",
        "suspended": "Suspended",
        "due_soon": "Due Soon",
        "churned": "Churned",
        "pending": "Pending",
    }
    return {mapping[key] for key in selected_segments if key in mapping}


def _csv_response(data: list[dict], filename: str) -> StreamingResponse:
    if not data:
        output = io.StringIO()
        output.write("No data available\n")
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=data[0].keys())
    writer.writeheader()
    writer.writerows(data)
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _append_query_flag(url: str, key: str, value: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{quote(key)}={quote(value)}"


def _latest_subscriber_sync_at(db: Session) -> datetime | None:
    latest = db.scalar(select(func.max(Subscriber.last_synced_at)))
    if latest is None:
        return None
    if latest.tzinfo is None:
        return latest.replace(tzinfo=UTC)
    return latest.astimezone(UTC)


def _billing_risk_cache_available(db: Session | object) -> bool:
    return hasattr(db, "query")


def _billing_risk_page_metrics(churn_rows: list[dict]) -> dict[str, int | float]:
    total_count = len(churn_rows)
    total_balance = round(sum(float(row.get("balance") or 0) for row in churn_rows), 2)
    overdue_values = [int(row["days_past_due"]) for row in churn_rows if isinstance(row.get("days_past_due"), int)]
    avg_days_overdue = round(sum(overdue_values) / len(overdue_values)) if overdue_values else 0
    return {
        "total_count": total_count,
        "total_balance": total_balance,
        "avg_days_overdue": avg_days_overdue,
    }


def _billing_risk_page_rows(
    db: Session,
    *,
    due_soon_days: int,
    high_balance_only: bool,
    segment: str | None,
    selected_segments: list[str],
    days_past_due: str | None,
    page: int,
    page_size: int,
    search: str | None,
    overdue_bucket: str | None,
    enterprise_only: bool = False,
    customer_segment: str | None = None,
    location: str | None = None,
    mrr_sort: str | None = None,
) -> tuple[list[dict], dict[str, int | float], bool]:
    requested_page_size = max(1, int(page_size))
    churn_rows = billing_risk_service.get_billing_risk_table(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        segment=segment,
        segments=selected_segments,
        days_past_due=days_past_due,
        limit=requested_page_size + 1,
        page=max(1, int(page)),
        page_size=requested_page_size + 1,
        search=search,
        overdue_bucket=overdue_bucket,
        enterprise_only=enterprise_only,
        customer_segment=customer_segment,
        location=location,
        mrr_sort=mrr_sort,
        enrich_visible_rows=False,
    )
    selected_labels = _segment_labels(selected_segments)
    if selected_labels:
        churn_rows = [row for row in churn_rows if str(row.get("risk_segment") or "") in selected_labels]
    has_next = len(churn_rows) > requested_page_size
    visible_rows = [dict(row) for row in churn_rows[:requested_page_size]]
    if not str(search or "").strip():
        billing_risk_service.enrich_billing_risk_rows(visible_rows)
    _enrich_missing_blocked_fields(visible_rows, force_live=False)
    return visible_rows, _billing_risk_page_metrics(visible_rows), has_next


def _billing_risk_rows_source(
    db: Session,
    *,
    due_soon_days: int,
    high_balance_only: bool,
    segment: str | None,
    selected_segments: list[str],
    days_past_due: str | None,
    search: str | None,
    overdue_bucket: str | None,
    enterprise_only: bool = False,
    customer_segment: str | None = None,
    location: str | None = None,
    mrr_sort: str | None = None,
    limit: int = 6000,
) -> tuple[list[dict], dict[str, object]]:
    normalized_customer_segment = customer_segment or "all"
    normalized_enterprise_only = bool(enterprise_only)
    normalized_location = (location if isinstance(location, str) else "").strip()
    if (
        settings.billing_risk_route_use_cache
        and _billing_risk_cache_available(db)
        and not normalized_enterprise_only
        and normalized_customer_segment == "all"
    ):
        rows = billing_risk_cache.all_cached_rows(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            selected_segments=selected_segments,
            days_past_due=days_past_due,
            search=search,
            overdue_bucket=overdue_bucket,
            location=normalized_location,
            limit=limit,
        )
        return rows, {
            "mode": "cache",
            "metadata": billing_risk_cache.cache_metadata(db),
        }
    rows = billing_risk_service.get_billing_risk_table(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        segment=segment,
        segments=selected_segments,
        days_past_due=days_past_due,
        limit=limit,
        search=search,
        overdue_bucket=overdue_bucket,
        enterprise_only=normalized_enterprise_only,
        customer_segment=normalized_customer_segment,
        location=normalized_location,
        mrr_sort=mrr_sort,
        enrich_visible_rows=False,
    )
    return rows, {"mode": "live", "metadata": {"row_count": len(rows)}}


def _billing_risk_cached_page_rows(
    db: Session,
    *,
    due_soon_days: int,
    high_balance_only: bool,
    selected_segments: list[str],
    days_past_due: str | None,
    page: int,
    page_size: int,
    search: str | None,
    overdue_bucket: str | None,
    location: str | None = None,
) -> tuple[list[dict], dict[str, int | float], bool]:
    cached_page = billing_risk_cache.list_cached_rows(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        selected_segments=selected_segments,
        days_past_due=days_past_due,
        page=page,
        page_size=page_size,
        search=search,
        overdue_bucket=overdue_bucket,
        location=location,
    )
    visible_rows = [dict(row) for row in cached_page.rows]
    _enrich_missing_blocked_fields(visible_rows, force_live=False)
    return visible_rows, cached_page.page_metrics, cached_page.has_next


def _billing_risk_initial_rows(
    churn_rows: list[dict],
    *,
    page_size: int,
) -> tuple[list[dict], dict[str, int | float], bool]:
    has_next = len(churn_rows) > page_size
    visible_rows = [dict(row) for row in churn_rows[:page_size]]
    billing_risk_service.enrich_billing_risk_rows(visible_rows)
    _enrich_missing_blocked_fields(visible_rows, force_live=False)
    return visible_rows, _billing_risk_page_metrics(visible_rows), has_next


def _retention_rep_options(db: Session) -> list[dict[str, str]]:
    target_team_names = {
        "helpdesk",
        "help desk",
        "enterprise sales",
        "customer support",
        "sales (call center)",
        "sales call center",
    }
    target_departments = {
        "helpdesk",
        "help_desk",
        "enterprise_sales",
        "customer_support",
        "sales_call_center",
    }
    options_by_person_id: dict[str, dict[str, str]] = {}
    fixed_options_by_label: dict[str, dict[str, str]] = {
        rep_name.casefold(): {
            "value": f"manual:{rep_name.casefold().replace(' ', '-')}",
            "label": rep_name,
            "team": "",
            "person_id": "",
        }
        for rep_name in RETENTION_FIXED_REP_NAMES
    }
    for rep_label, team_label in RETENTION_REP_TEAM_OVERRIDES.items():
        formatted_label = " ".join(part.capitalize() for part in rep_label.split())
        fixed_options_by_label.setdefault(
            rep_label,
            {
                "value": f"manual:{rep_label.replace(' ', '-')}",
                "label": formatted_label,
                "team": team_label,
                "person_id": "",
            },
        )

    service_team_rows = db.execute(
        select(Person.id, Person.display_name, Person.first_name, Person.last_name, Person.email, ServiceTeam.name)
        .select_from(ServiceTeamMember)
        .join(ServiceTeam, ServiceTeam.id == ServiceTeamMember.team_id)
        .join(Person, Person.id == ServiceTeamMember.person_id)
        .where(
            ServiceTeam.is_active.is_(True),
            ServiceTeamMember.is_active.is_(True),
            Person.is_active.is_(True),
        )
    ).all()
    for person_id, display_name, first_name, last_name, email, team_name in service_team_rows:
        team_key = str(team_name or "").strip().lower()
        team_department_key = team_key.replace(" ", "_")
        if team_key not in target_team_names and team_department_key not in target_departments:
            continue
        label = str(display_name or f"{first_name or ''} {last_name or ''}".strip() or email or "Unnamed rep").strip()
        team_label = "" if label.casefold() in RETENTION_REP_LABEL_ONLY_NAMES else str(team_name or "").strip()
        team_label = RETENTION_REP_TEAM_OVERRIDES.get(label.casefold(), team_label)
        options_by_person_id[str(person_id)] = {
            "value": str(person_id),
            "label": label,
            "team": team_label,
            "person_id": str(person_id),
        }

    crm_team_rows = db.execute(
        select(Person.id, Person.display_name, Person.first_name, Person.last_name, Person.email, CrmTeam.name)
        .select_from(CrmAgentTeam)
        .join(CrmTeam, CrmTeam.id == CrmAgentTeam.team_id)
        .join(CrmAgent, CrmAgent.id == CrmAgentTeam.agent_id)
        .join(Person, Person.id == CrmAgent.person_id)
        .where(
            CrmTeam.is_active.is_(True),
            CrmAgentTeam.is_active.is_(True),
            CrmAgent.is_active.is_(True),
            Person.is_active.is_(True),
        )
    ).all()
    for person_id, display_name, first_name, last_name, email, team_name in crm_team_rows:
        team_key = str(team_name or "").strip().lower()
        if team_key not in target_team_names:
            continue
        label = str(display_name or f"{first_name or ''} {last_name or ''}".strip() or email or "Unnamed rep").strip()
        team_label = "" if label.casefold() in RETENTION_REP_LABEL_ONLY_NAMES else str(team_name or "").strip()
        team_label = RETENTION_REP_TEAM_OVERRIDES.get(label.casefold(), team_label)
        options_by_person_id.setdefault(
            str(person_id),
            {
                "value": str(person_id),
                "label": label,
                "team": team_label,
                "person_id": str(person_id),
            },
        )

    # Resolve manual rep cards to real people where possible so engagements can persist person IDs.
    manual_names = set(RETENTION_REP_TEAM_OVERRIDES.keys())
    first_name_candidates = {name.split()[0] for name in manual_names if name.split()}
    possible_people = db.execute(
        select(Person.id, Person.display_name, Person.first_name, Person.last_name, Person.email).where(
            Person.is_active.is_(True),
            func.lower(func.coalesce(Person.first_name, "")).in_(first_name_candidates),
        )
    ).all()
    for row in possible_people:
        if len(row) < 5:
            continue
        person_id, display_name, first_name, last_name, email = row[:5]
        label = str(display_name or f"{first_name or ''} {last_name or ''}".strip() or email or "").strip()
        normalized_label = label.casefold()
        if normalized_label not in manual_names:
            continue
        team_label = RETENTION_REP_TEAM_OVERRIDES[normalized_label]
        fixed_options_by_label[normalized_label] = {
            "value": str(person_id),
            "label": label,
            "team": team_label,
            "person_id": str(person_id),
        }

    for option in options_by_person_id.values():
        fixed_options_by_label[option["label"].casefold()] = option

    return sorted(
        fixed_options_by_label.values(),
        key=lambda option: (option["team"].casefold(), option["label"].casefold()),
    )


def _parse_follow_up_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid follow-up date") from exc


def _retention_engagement_payload(row: CustomerRetentionEngagement) -> dict[str, str | None]:
    return {
        "id": str(row.id),
        "customerId": row.customer_external_id,
        "customerName": row.customer_name,
        "outcome": row.outcome,
        "note": row.note or "",
        "followUp": row.follow_up_date.isoformat() if row.follow_up_date else "",
        "rep": row.rep_label or "",
        "repPersonId": str(row.rep_person_id) if row.rep_person_id else "",
        "createdAt": row.created_at.isoformat() if row.created_at else "",
    }


def _retention_engagements_by_customer(db: Session, customer_ids: list[str]) -> dict[str, list[dict[str, str | None]]]:
    normalized_ids = sorted(
        {str(customer_id or "").strip() for customer_id in customer_ids if str(customer_id or "").strip()}
    )
    if not normalized_ids:
        return {}
    rows = db.scalars(
        select(CustomerRetentionEngagement)
        .where(
            CustomerRetentionEngagement.customer_external_id.in_(normalized_ids),
            CustomerRetentionEngagement.is_active.is_(True),
        )
        .order_by(CustomerRetentionEngagement.created_at.desc())
    ).all()
    grouped: dict[str, list[dict[str, str | None]]] = {customer_id: [] for customer_id in normalized_ids}
    for row in rows:
        grouped.setdefault(row.customer_external_id, []).append(_retention_engagement_payload(row))
    return grouped


def _retention_active_customer_ids(db: Session) -> list[str]:
    rows = db.execute(
        select(CustomerRetentionEngagement.customer_external_id)
        .where(CustomerRetentionEngagement.is_active.is_(True))
        .group_by(CustomerRetentionEngagement.customer_external_id)
        .order_by(func.max(CustomerRetentionEngagement.created_at).desc())
    ).all()
    customer_ids: list[str] = []
    for (customer_id,) in rows:
        normalized = str(customer_id or "").strip()
        if normalized:
            customer_ids.append(normalized)
    return customer_ids


def _retention_search_customer_ids(db: Session, search: str) -> list[str]:
    term = str(search or "").strip()
    if not term:
        return []
    if not hasattr(db, "query"):
        return []
    like_term = f"%{term}%"
    rows = (
        db.query(CustomerRetentionEngagement.customer_external_id)
        .filter(CustomerRetentionEngagement.is_active.is_(True))
        .filter(
            or_(
                CustomerRetentionEngagement.customer_external_id.ilike(like_term),
                CustomerRetentionEngagement.customer_name.ilike(like_term),
                CustomerRetentionEngagement.note.ilike(like_term),
                CustomerRetentionEngagement.rep_label.ilike(like_term),
            )
        )
        .order_by(CustomerRetentionEngagement.created_at.desc())
        .all()
    )
    customer_ids: list[str] = []
    seen: set[str] = set()
    for (customer_id,) in rows:
        normalized = str(customer_id or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        customer_ids.append(normalized)
    return customer_ids


def _retention_customer_id(row: dict) -> str:
    return str(row.get("_external_id") or row.get("subscriber_id") or row.get("_subscriber_number") or "").strip()


def _export_text(value: object, default: str = "-") -> str:
    text = str(value or "").strip()
    return text or default


def _export_currency(value: object) -> str:
    try:
        amount = float(str(value or 0))
    except (TypeError, ValueError):
        amount = 0.0
    return f"₦{amount:,.2f}"


def _export_int(value: object) -> int:
    try:
        return int(float(str(value or 0)))
    except (TypeError, ValueError):
        return 0


def _blocked_for_export_label(row: dict) -> str:
    blocked_for_days = row.get("blocked_for_days")
    if blocked_for_days in (None, ""):
        blocked_date_text = str(row.get("blocked_date") or "").strip()[:10]
        try:
            blocked_date = date.fromisoformat(blocked_date_text)
        except ValueError:
            return "-"
        blocked_for_days = max(0, (datetime.now(UTC).date() - blocked_date).days)
    try:
        days = int(float(str(blocked_for_days)))
    except (TypeError, ValueError):
        return "-"
    return f"Blocked for {days} days"


def _coerce_blocked_days_value(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return int(float(normalized))
    except (TypeError, ValueError):
        pass
    match = re.search(r"-?\d+\.?\d*", normalized)
    if match is None:
        return None
    try:
        return int(float(match.group(0)))
    except (TypeError, ValueError):
        return None


def _normalize_blocked_date_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {"n/a", "na", "-", "none", "null", "0000-00-00"}:
        return ""
    return text


def _blocked_days_buckets(churn_rows: list[dict]) -> list[dict[str, int | str]]:
    blocked_counts = {
        "Blocked 0-7 Days": 0,
        "Blocked 8-30 Days": 0,
        "Blocked 31-60 Days": 0,
        "Blocked 61+ Days": 0,
    }
    today = datetime.now(UTC).date()

    def _coerce_row_blocked_days(row: dict) -> int | None:
        blocked_days_value = _coerce_blocked_days_value(row.get("blocked_for_days"))
        if blocked_days_value is not None:
            return blocked_days_value
        blocked_date_text = str(row.get("blocked_date") or "").strip()
        parsed_blocked_date = billing_risk_service._parse_iso_date_text(blocked_date_text)
        if parsed_blocked_date is not None:
            return max(0, (today - parsed_blocked_date).days)
        days_past_due = _coerce_blocked_days_value(row.get("days_past_due"))
        if days_past_due is not None and days_past_due >= 0:
            return days_past_due
        return None

    for row in churn_rows:
        blocked_days = _coerce_row_blocked_days(row)
        if blocked_days is None:
            continue

        segment_value = str(row.get("risk_segment") or "").strip()
        if segment_value not in {"Suspended", "Churned"}:
            continue

        if blocked_days <= 7:
            blocked_counts["Blocked 0-7 Days"] += 1
        elif blocked_days <= 30:
            blocked_counts["Blocked 8-30 Days"] += 1
        elif blocked_days <= 60:
            blocked_counts["Blocked 31-60 Days"] += 1
        else:
            blocked_counts["Blocked 61+ Days"] += 1

    return [{"label": label, "count": count} for label, count in blocked_counts.items()]


def _safe_live_blocked_dates(
    external_ids: list[str],
    *,
    force_live: bool = False,
    blocking_only_external_ids: list[str] | None = None,
) -> dict[str, str]:
    if not external_ids:
        return {}
    try:
        return billing_risk_service.get_live_blocked_dates(
            external_ids,
            force_live=force_live,
            blocking_only_external_ids=blocking_only_external_ids,
        )
    except TypeError as exc:
        # Keep compatibility with monkeypatched/test doubles that don't accept force_live kwarg.
        if "force_live" in str(exc) or "blocking_only_external_ids" in str(exc):
            try:
                return billing_risk_service.get_live_blocked_dates(external_ids)
            except Exception:
                return {}
        return {}
    except Exception:
        return {}


def _enrich_missing_blocked_fields(churn_rows: list[dict], *, force_live: bool = False) -> None:
    if not churn_rows:
        return

    today = datetime.now(UTC).date()

    def _status_and_segment_rules(row: dict) -> tuple[bool, bool]:
        subscriber_status = str(row.get("subscriber_status") or "").strip().lower()
        risk_segment = str(row.get("risk_segment") or "").strip()
        should_hide_blocked_for = subscriber_status == "active"
        is_blocked_like = subscriber_status in {"blocked", "suspended"} or risk_segment in {"Suspended", "Churned"}
        return should_hide_blocked_for, is_blocked_like

    def _infer_from_blocking_period(row: dict) -> int | None:
        blocking_period = _coerce_blocked_days_value(row.get("blocking_period"))
        days_past_due = _coerce_blocked_days_value(row.get("days_past_due"))
        if blocking_period is None or days_past_due is None:
            return None
        return max(0, days_past_due - blocking_period)

    def _infer_from_customer_last_update(row: dict) -> tuple[int, str] | None:
        parsed = billing_risk_service._parse_iso_date_text(str(row.get("_customer_last_update") or ""))
        if parsed is None:
            return None
        return max(0, (today - parsed).days), parsed.strftime("%Y-%m-%d")

    def _is_invalid_blocked_date_fallback(row: dict, blocked_date_text: str) -> bool:
        if not blocked_date_text:
            return False
        billing_start_text = _normalize_blocked_date_text(row.get("billing_start_date"))
        return bool(billing_start_text and blocked_date_text == billing_start_text)

    external_ids = sorted(
        {str(row.get("_external_id") or "").strip() for row in churn_rows if str(row.get("_external_id") or "").strip()}
    )
    # Always prefer live Splynx blocking_date for the UI Blocked Date cell.
    live_blocked_dates = _safe_live_blocked_dates(
        external_ids,
        force_live=force_live,
    )
    target_rows: list[tuple[dict, str]] = []
    missing_external_ids: set[str] = set()
    for row in churn_rows:
        external_id = str(row.get("_external_id") or "").strip()
        if external_id and external_id in live_blocked_dates:
            live_blocked_date = live_blocked_dates.get(external_id, "")
            if live_blocked_date:
                row["blocked_date"] = live_blocked_date
                should_hide_blocked_for, is_blocked_like = _status_and_segment_rules(row)
                if should_hide_blocked_for:
                    row["blocked_for_days"] = None
                else:
                    parsed_live_blocked_date = billing_risk_service._parse_iso_date_text(live_blocked_date)
                    if parsed_live_blocked_date is not None and is_blocked_like:
                        row["blocked_for_days"] = max(0, (today - parsed_live_blocked_date).days)
                    else:
                        row["blocked_for_days"] = None
                # Live Splynx blocked date is authoritative for this row.
                continue

        should_hide_blocked_for, is_blocked_like = _status_and_segment_rules(row)
        if should_hide_blocked_for:
            row["blocked_for_days"] = None
            continue

        blocked_date_text = _normalize_blocked_date_text(row.get("blocked_date"))
        if _is_invalid_blocked_date_fallback(row, blocked_date_text):
            row["blocked_date"] = ""
            blocked_date_text = ""
        blocked_for_days = _coerce_blocked_days_value(row.get("blocked_for_days"))
        if blocked_for_days is not None:
            row["blocked_for_days"] = blocked_for_days
        else:
            parsed_blocked_date = billing_risk_service._parse_iso_date_text(blocked_date_text)
            if parsed_blocked_date is not None:
                blocked_for_days = max(0, (today - parsed_blocked_date).days)
                row["blocked_for_days"] = blocked_for_days

        if not is_blocked_like and not blocked_date_text:
            continue
        if blocked_date_text and blocked_for_days is not None:
            continue
        if blocked_for_days is None:
            inferred_blocked_days = _infer_from_blocking_period(row)
            if inferred_blocked_days is not None and is_blocked_like:
                row["blocked_for_days"] = inferred_blocked_days
                blocked_for_days = inferred_blocked_days
                if not blocked_date_text and inferred_blocked_days > 0:
                    row["blocked_date"] = (today - timedelta(days=inferred_blocked_days)).strftime("%Y-%m-%d")
                    blocked_date_text = row["blocked_date"]
            else:
                days_past_due = _coerce_blocked_days_value(row.get("days_past_due"))
                if days_past_due is not None and days_past_due >= 0 and is_blocked_like:
                    row["blocked_for_days"] = days_past_due
                    blocked_for_days = days_past_due
                else:
                    inferred_from_update = _infer_from_customer_last_update(row)
                    if inferred_from_update is not None and is_blocked_like:
                        inferred_days, inferred_date_text = inferred_from_update
                        row["blocked_for_days"] = inferred_days
                        blocked_for_days = inferred_days
                        if not blocked_date_text:
                            row["blocked_date"] = inferred_date_text
                            blocked_date_text = inferred_date_text

        if blocked_for_days is not None:
            continue

        if not external_id:
            continue
        target_rows.append((row, external_id))
        missing_external_ids.add(external_id)

    blocked_dates = {
        external_id: live_blocked_dates.get(external_id, "") for external_id in sorted(missing_external_ids)
    }
    for row, external_id in target_rows:
        should_hide_blocked_for, is_blocked_like = _status_and_segment_rules(row)
        if should_hide_blocked_for:
            row["blocked_for_days"] = None
            continue

        row_blocked_date_text = _normalize_blocked_date_text(row.get("blocked_date"))
        if _is_invalid_blocked_date_fallback(row, row_blocked_date_text):
            row["blocked_date"] = ""
            row_blocked_date_text = ""
        blocked_date_text = row_blocked_date_text
        if not row_blocked_date_text:
            live_blocked_date = blocked_dates.get(external_id, "")
            if live_blocked_date:
                row["blocked_date"] = live_blocked_date
                row_blocked_date_text = live_blocked_date
            blocked_date_text = row_blocked_date_text
        blocked_for_days = _coerce_blocked_days_value(row.get("blocked_for_days"))
        if blocked_for_days in (None, ""):
            parsed_blocked_date = billing_risk_service._parse_iso_date_text(blocked_date_text)
            if parsed_blocked_date is not None:
                row["blocked_for_days"] = max(0, (today - parsed_blocked_date).days)
                continue
            inferred_blocked_days = _infer_from_blocking_period(row)
            if inferred_blocked_days is not None and is_blocked_like:
                row["blocked_for_days"] = inferred_blocked_days
                if not blocked_date_text and inferred_blocked_days > 0:
                    row["blocked_date"] = (today - timedelta(days=inferred_blocked_days)).strftime("%Y-%m-%d")
                continue
            days_past_due = _coerce_blocked_days_value(row.get("days_past_due"))
            if days_past_due is not None and days_past_due >= 0 and is_blocked_like:
                row["blocked_for_days"] = days_past_due
                continue
            inferred_from_update = _infer_from_customer_last_update(row)
            if inferred_from_update is not None and is_blocked_like:
                inferred_days, inferred_date_text = inferred_from_update
                row["blocked_for_days"] = inferred_days
                if not blocked_date_text:
                    row["blocked_date"] = inferred_date_text


def _billing_risk_visible_export_rows(
    db: Session,
    churn_rows: list[dict],
) -> list[dict[str, object]]:
    engagement_history = _retention_engagements_by_customer(db, [_retention_customer_id(row) for row in churn_rows])
    export_rows: list[dict[str, object]] = []
    for row in churn_rows:
        customer_id = _retention_customer_id(row)
        customer_engagements = engagement_history.get(customer_id) or []
        latest_engagement = customer_engagements[0] if customer_engagements else None
        export_rows.append(
            {
                "Name": _export_text(row.get("name")),
                "Phone": _export_text(row.get("phone")),
                "City": _export_text(row.get("city")),
                "Street": _export_text(row.get("street")),
                "Area": _export_text(row.get("area")),
                "Plan": _export_text(row.get("plan")),
                "MRR Total": _export_currency(row.get("mrr_total")),
                "Status": _export_text(row.get("subscriber_status")),
                "Risk Segment": _export_text(row.get("risk_segment")),
                "Billing Start Date": _export_text(row.get("billing_start_date")),
                "Blocked Date": _export_text(row.get("blocked_date")),
                "Blocked For": _blocked_for_export_label(row),
                "Tickets Open": _export_int(row.get("open_tickets")),
                "Tickets Closed": _export_int(row.get("closed_tickets")),
                "Tickets Total": _export_int(row.get("total_tickets")),
                "Last Outcome": _export_text(latest_engagement.get("outcome") if latest_engagement else None),
                "Follow-up": _export_text(latest_engagement.get("followUp") if latest_engagement else None),
            }
        )
    return export_rows


def _pipeline_stage_from_engagement(engagement: dict[str, str | None] | None, row: dict | None = None) -> str:
    if not engagement:
        risk_segment = str((row or {}).get("risk_segment") or "").strip()
        if risk_segment == "Churned":
            return "Lost"
        return "Contacted"
    outcome = str(engagement.get("outcome") or "").strip()
    follow_up = str(engagement.get("followUp") or "").strip()
    if outcome in {"Renewing", "Paid", "Resolved"}:
        return "Resolved"
    if outcome in {"Churning", "Do Not Reach Out"}:
        return "Lost"
    if outcome == "Promised to Pay":
        return "Promised to Pay"
    if follow_up:
        return "Follow-up Pending"
    return "Contacted"


def _days_until_follow_up(follow_up: str, *, today: date | None = None) -> int | None:
    try:
        follow_up_date = date.fromisoformat(str(follow_up or "").strip())
    except ValueError:
        return None
    return (follow_up_date - (today or datetime.now(UTC).date())).days


def _retention_rows_with_pipeline(
    tracker_rows: list[dict],
    engagement_history: dict[str, list[dict[str, str | None]]],
) -> list[dict]:
    enriched_rows: list[dict] = []
    for row in tracker_rows:
        customer_id = _retention_customer_id(row)
        customer_engagements = engagement_history.get(customer_id) or []
        latest_engagement = customer_engagements[0] if customer_engagements else None
        candidate = dict(row)
        candidate["pipeline_stage"] = _pipeline_stage_from_engagement(latest_engagement, candidate)
        if latest_engagement:
            candidate["latest_follow_up"] = latest_engagement.get("followUp") or ""
            candidate["latest_rep"] = latest_engagement.get("rep") or ""
        else:
            candidate["latest_follow_up"] = ""
            candidate["latest_rep"] = ""
        days_until_follow_up = _days_until_follow_up(str(candidate.get("latest_follow_up") or ""))
        candidate["follow_up_due_label"] = ""
        if days_until_follow_up is not None:
            if days_until_follow_up < 0:
                candidate["follow_up_due_label"] = f"{abs(days_until_follow_up)} days overdue"
            elif days_until_follow_up == 0:
                candidate["follow_up_due_label"] = "Due today"
            else:
                candidate["follow_up_due_label"] = f"Due in {days_until_follow_up} days"
        enriched_rows.append(candidate)
    return enriched_rows


def _filter_excluded_retention_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if not _is_excluded_retention_customer(row.get("name"))]


def _retention_follow_up_reminders(
    tracker_rows: list[dict],
    engagement_history: dict[str, list[dict[str, str | None]]],
) -> list[dict[str, object]]:
    reminders: list[dict[str, object]] = []
    for row in tracker_rows:
        customer_id = _retention_customer_id(row)
        customer_engagements = engagement_history.get(customer_id) or []
        latest_engagement = customer_engagements[0] if customer_engagements else None
        if not latest_engagement:
            continue
        stage = _pipeline_stage_from_engagement(latest_engagement, row)
        if stage in {"Resolved", "Lost"}:
            continue
        follow_up = str(latest_engagement.get("followUp") or "").strip()
        days_until = _days_until_follow_up(follow_up)
        if days_until is None or days_until > 0:
            continue
        reminders.append(
            {
                "customer_id": customer_id,
                "customer_name": row.get("name") or "Unknown Customer",
                "phone": row.get("phone") or row.get("email") or "",
                "follow_up": follow_up,
                "days_overdue": abs(days_until),
                "due_label": "Due today" if days_until == 0 else f"{abs(days_until)} days overdue",
                "outcome": latest_engagement.get("outcome") or "",
                "rep": latest_engagement.get("rep") or "",
                "stage": stage,
            }
        )
    reminders.sort(
        key=lambda reminder: (
            -int(reminder["days_overdue"]) if isinstance(reminder["days_overdue"], int) else 0,
            str(reminder["customer_name"]).casefold(),
        )
    )
    return reminders


def _person_id_from_user(user: dict) -> str | None:
    return str(user.get("person_id") or user.get("id") or "").strip() or None


def _optional_uuid(value: object):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return coerce_uuid(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid person reference") from exc


def _resolve_subscriber_ids(db: Session, values: list[str]) -> list[str]:
    normalized_values = [str(value).strip() for value in values if str(value).strip()]
    if not normalized_values:
        return []

    resolved_ids: list[str] = []
    unresolved_values: list[str] = []
    for value in normalized_values:
        try:
            resolved_ids.append(str(coerce_uuid(value)))
        except ValueError:
            unresolved_values.append(value)

    if unresolved_values:
        rows = db.execute(
            select(Subscriber.external_id, Subscriber.id).where(Subscriber.external_id.in_(unresolved_values))
        ).all()
        id_by_external_id = {str(external_id or "").strip(): str(subscriber_id) for external_id, subscriber_id in rows}
        for value in unresolved_values:
            subscriber_id = id_by_external_id.get(value)
            if subscriber_id:
                resolved_ids.append(subscriber_id)

    unique_ids: list[str] = []
    seen: set[str] = set()
    for value in resolved_ids:
        if value in seen:
            continue
        seen.add(value)
        unique_ids.append(value)
    return unique_ids


def _retention_tracker_rows(churn_rows: list[dict], *, limit: int = 100) -> list[dict]:
    segment_priority = {
        "Suspended": 0,
        "Due Soon": 1,
        "Pending": 3,
        "Churned": 4,
    }

    def _int_value(value: object) -> int:
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value.strip())
        return 0

    def _stage_for(row: dict) -> str:
        segment = str(row.get("risk_segment") or "")
        days_past_due = _int_value(row.get("days_past_due"))
        if segment == "Churned":
            return "Win-back review"
        if segment == "Suspended" or days_past_due >= 30:
            return "Recovery priority"
        if segment in {"Due Soon", "Pending"}:
            return "Retention watch"
        return "Monitor"

    def _action_for(row: dict) -> str:
        segment = str(row.get("risk_segment") or "")
        days_past_due = _int_value(row.get("days_past_due"))
        balance = float(row.get("balance") or 0)
        if segment == "Churned":
            return "Confirm cancellation reason and queue a win-back offer."
        if segment == "Suspended":
            return "Call today, confirm payment path, and document restore conditions."
        if days_past_due >= 30 or balance >= 50000:
            return "Escalate to collections with a retention note before disconnection."
        if segment in {"Due Soon", "Pending"}:
            return "Confirm next invoice readiness and update customer contact status."
        return "Review account notes and keep in the retention watch list."

    tracker_rows = []
    for row in churn_rows:
        if _is_excluded_retention_customer(row.get("name")):
            continue
        candidate = dict(row)
        candidate["retention_stage"] = _stage_for(candidate)
        candidate["recommended_action"] = _action_for(candidate)
        candidate["days_past_due_display"] = _int_value(candidate.get("days_past_due"))
        tracker_rows.append(candidate)

    tracker_rows.sort(
        key=lambda row: (
            segment_priority.get(str(row.get("risk_segment") or ""), 99),
            -_int_value(row.get("days_past_due")),
            -float(row.get("balance") or 0),
            str(row.get("name") or "").casefold(),
        )
    )
    return tracker_rows[:limit]


def _retention_billing_rows_for_customer_ids(
    db: Session,
    *,
    customer_ids: list[str],
    due_soon_days: int,
    high_balance_only: bool = False,
    segment: str | None = None,
    selected_segments: list[str] | None = None,
    days_past_due: str | None = None,
    search: str | None = None,
    limit: int = 6000,
) -> list[dict]:
    normalized_customer_ids = sorted(
        {str(customer_id or "").strip() for customer_id in customer_ids if str(customer_id or "").strip()}
    )
    if not normalized_customer_ids:
        return []
    if settings.customer_retention_route_use_cache and _billing_risk_cache_available(db):
        return billing_risk_cache.cached_rows_by_external_ids(
            db,
            normalized_customer_ids,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            selected_segments=selected_segments,
            days_past_due=days_past_due,
            search=search,
            limit=limit,
        )
    rows = billing_risk_service.get_billing_risk_table(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        segment=segment,
        segments=selected_segments,
        days_past_due=days_past_due,
        limit=limit,
        search=search,
        enrich_visible_rows=False,
    )
    return [row for row in rows if _retention_customer_id(row) in set(normalized_customer_ids)]


def _retention_tracker_kpis(churn_rows: list[dict]) -> dict[str, int | float]:
    recovery_segments = {"Suspended", "Due Soon"}
    tracked_count = len(churn_rows)
    recovery_priority_count = sum(1 for row in churn_rows if str(row.get("risk_segment") or "") in recovery_segments)
    due_soon_count = sum(1 for row in churn_rows if str(row.get("risk_segment") or "") in {"Due Soon", "Pending"})
    recovered_count = sum(1 for row in churn_rows if str(row.get("pipeline_stage") or "") == "Resolved")
    lost_count = sum(1 for row in churn_rows if str(row.get("pipeline_stage") or "") == "Lost")
    winback_pool_count = int(recovered_count) + int(lost_count)
    winback_rate = round((int(recovered_count) / winback_pool_count) * 100, 1) if winback_pool_count else 0.0
    churn_rate = round((int(lost_count) / tracked_count) * 100, 1) if tracked_count else 0.0
    revenue_at_risk = round(sum(float(row.get("balance") or 0) for row in churn_rows), 2)
    high_balance_count = sum(1 for row in churn_rows if bool(row.get("is_high_balance_risk")))
    return {
        "tracked_count": tracked_count,
        "recovery_priority_count": recovery_priority_count,
        "due_soon_count": due_soon_count,
        "winback_rate": winback_rate,
        "recovered_count": recovered_count,
        "lost_count": lost_count,
        "churn_rate": churn_rate,
        "revenue_at_risk": revenue_at_risk,
        "high_balance_count": high_balance_count,
    }


@router.get("/subscribers/billing-risk", response_class=HTMLResponse)
def subscriber_billing_risk(
    request: Request,
    db: Session = Depends(get_db),
    due_soon_days: int = Query(7, ge=1, le=30),
    overdue_invoice_days: int = Query(30, ge=1, le=180),
    high_balance_only: bool = Query(False),
    segment: str | None = Query(None),
    segments: list[str] = Query(default=[]),
    days_past_due: str | None = Query(None),
    bucket: str | None = Query("all"),
    search: str | None = Query(None),
    enterprise_only: bool = Query(False),
    customer_segment: str | None = Query(None),
    location: str | None = Query(None),
    mrr_sort: str | None = Query(None),
):
    user = get_current_user(request)
    query_segments = request.query_params.getlist("segments")
    query_segment = request.query_params.get("segment")
    query_days_past_due = request.query_params.get("days_past_due")
    query_bucket = request.query_params.get("bucket")
    normalized_bucket = (
        query_bucket if query_bucket is not None else (bucket if isinstance(bucket, str) else "all")
    ).strip() or "all"
    query_search = request.query_params.get("search")
    normalized_search = query_search if query_search is not None else (search if isinstance(search, str) else None)
    normalized_customer_segment = "all"
    normalized_enterprise_only = False
    normalized_location = (
        request.query_params.get("location") or (location if isinstance(location, str) else "")
    ).strip()
    query_mrr_sort = request.query_params.get("mrr_sort")
    normalized_mrr_sort = (
        (query_mrr_sort if query_mrr_sort is not None else (mrr_sort if isinstance(mrr_sort, str) else ""))
        .strip()
        .lower()
    )
    selected_segments = _normalize_segment_filters(
        query_segments if query_segments else segments,
        query_segment or segment,
    )
    selected_labels = _segment_labels(selected_segments)
    cache_eligible = (
        settings.billing_risk_route_use_cache
        and _billing_risk_cache_available(db)
        and not normalized_enterprise_only
        and normalized_customer_segment == "all"
    )
    if cache_eligible:
        cached_page = billing_risk_cache.list_cached_rows(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            selected_segments=selected_segments,
            days_past_due=query_days_past_due or days_past_due,
            search=normalized_search,
            overdue_bucket=normalized_bucket,
            location=normalized_location,
            page=1,
            page_size=50,
        )
        page_rows = [dict(row) for row in cached_page.rows]
        page_metrics = cached_page.page_metrics
        has_next = cached_page.has_next
        full_metric_rows: list[dict] = []
        end_read_only_transaction(db)
        billing_risk_route_state = {
            "mode": "cache",
            "metadata": billing_risk_cache.cache_metadata(db),
            "cached_metrics": True,
        }
    else:
        initial_rows, _initial_route_state = _billing_risk_rows_source(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            segment=segment,
            selected_segments=selected_segments,
            days_past_due=query_days_past_due or days_past_due,
            search=normalized_search,
            overdue_bucket=normalized_bucket,
            enterprise_only=normalized_enterprise_only,
            customer_segment=normalized_customer_segment,
            location=normalized_location,
            mrr_sort=normalized_mrr_sort,
            limit=51,
        )
        if selected_labels:
            initial_rows = [row for row in initial_rows if str(row.get("risk_segment") or "") in selected_labels]
        full_metric_rows, billing_risk_route_state = _billing_risk_rows_source(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            segment=segment,
            selected_segments=selected_segments,
            days_past_due=query_days_past_due or days_past_due,
            search=normalized_search,
            overdue_bucket=normalized_bucket,
            enterprise_only=normalized_enterprise_only,
            customer_segment=normalized_customer_segment,
            location=normalized_location,
            mrr_sort=normalized_mrr_sort,
            limit=10000,
        )
        end_read_only_transaction(db)
        if selected_labels:
            full_metric_rows = [
                row for row in full_metric_rows if str(row.get("risk_segment") or "") in selected_labels
            ]
        page_rows, page_metrics, has_next = _billing_risk_initial_rows(initial_rows, page_size=50)
    overdue_invoices = billing_risk_service.get_overdue_invoices_table(
        db,
        min_days_past_due=overdue_invoice_days,
        limit=250,
    )
    billing_risk_cache_metadata = billing_risk_route_state.get("metadata") or {"row_count": len(full_metric_rows)}
    sidebar_stats = get_sidebar_stats(db)
    last_synced_at = _latest_subscriber_sync_at(db)
    rep_options = _retention_rep_options(db)
    outreach_targets = outreach_channel_target_options(db)
    if billing_risk_route_state.get("cached_metrics"):
        overdue_invoice_balance = round(sum(float(row.get("total_balance_due") or 0) for row in overdue_invoices), 2)
        kpis = billing_risk_cache.summary_cached(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            selected_segments=selected_segments,
            days_past_due=query_days_past_due or days_past_due,
            search=normalized_search,
            overdue_bucket=normalized_bucket,
            location=normalized_location,
            overdue_invoice_balance=overdue_invoice_balance,
        )
        segment_breakdown = billing_risk_cache.segment_breakdown_cached(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            selected_segments=selected_segments,
            days_past_due=query_days_past_due or days_past_due,
            search=normalized_search,
            overdue_bucket=normalized_bucket,
            location=normalized_location,
        )
        aging_buckets = billing_risk_cache.aging_buckets_cached(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            selected_segments=selected_segments,
            days_past_due=query_days_past_due or days_past_due,
            search=normalized_search,
            overdue_bucket=normalized_bucket,
            location=normalized_location,
        )
    else:
        kpis = billing_risk_service.get_billing_risk_summary(full_metric_rows, overdue_invoices)
        segment_breakdown = billing_risk_service.get_billing_risk_segment_breakdown(full_metric_rows)
        aging_buckets = billing_risk_service.get_billing_risk_aging_buckets(full_metric_rows)

    export_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
            "bucket": normalized_bucket,
            "search": normalized_search or "",
            "location": normalized_location,
            "mrr_sort": normalized_mrr_sort,
        },
        doseq=True,
    )
    retention_tracker_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "high_balance_only": str(high_balance_only).lower(),
            "segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
            "bucket": normalized_bucket,
            "search": normalized_search or "",
            "location": normalized_location,
            "mrr_sort": normalized_mrr_sort,
        },
        doseq=True,
    )
    refresh_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "segment": segment or "",
            "segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due or "",
            "bucket": normalized_bucket,
            "search": normalized_search or "",
            "location": normalized_location,
            "mrr_sort": normalized_mrr_sort,
        },
        doseq=True,
    )
    segment_all_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "days_past_due": query_days_past_due or days_past_due or "",
            "bucket": normalized_bucket,
            "search": normalized_search or "",
            "location": normalized_location,
            "mrr_sort": normalized_mrr_sort,
        },
        doseq=True,
    )
    segment_due_soon_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "days_past_due": query_days_past_due or days_past_due or "",
            "bucket": normalized_bucket,
            "search": normalized_search or "",
            "location": normalized_location,
            "mrr_sort": normalized_mrr_sort,
            "segment": "overdue",
        },
        doseq=True,
    )
    segment_suspended_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "days_past_due": query_days_past_due or days_past_due or "",
            "bucket": normalized_bucket,
            "search": normalized_search or "",
            "location": normalized_location,
            "mrr_sort": normalized_mrr_sort,
            "segment": "suspended",
        },
        doseq=True,
    )
    return templates.TemplateResponse(
        "admin/reports/subscriber_billing_risk.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": sidebar_stats,
            "active_page": "subscriber-billing-risk",
            "active_menu": "reports",
            "kpis": kpis,
            "segment_breakdown": segment_breakdown,
            "aging_buckets": aging_buckets,
            "churn_rows": page_rows,
            "overdue_invoices": overdue_invoices,
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": high_balance_only,
            "selected_segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
            "export_query": export_query,
            "retention_tracker_query": retention_tracker_query,
            "refresh_query": refresh_query,
            "segment_all_query": segment_all_query,
            "segment_due_soon_query": segment_due_soon_query,
            "segment_suspended_query": segment_suspended_query,
            "last_synced_at": last_synced_at,
            "billing_risk_cache": billing_risk_cache_metadata,
            "billing_risk_route_mode": billing_risk_route_state.get("mode", "live"),
            "csrf_token": get_csrf_token(request),
            "refresh_started": request.query_params.get("refresh_started") == "1",
            "refresh_error": request.query_params.get("refresh_error"),
            "live_page": 1,
            "live_page_size": 50,
            "live_has_next": has_next,
            "live_search": normalized_search or "",
            "live_location": normalized_location,
            "live_location_options": sorted(
                billing_risk_cache.location_options_cached(
                    db,
                    due_soon_days=due_soon_days,
                    high_balance_only=high_balance_only,
                    selected_segments=selected_segments,
                    days_past_due=query_days_past_due or days_past_due,
                    search=normalized_search,
                    overdue_bucket=normalized_bucket,
                )
                if billing_risk_route_state.get("cached_metrics")
                else {
                    str(row.get("location") or "").strip()
                    for row in full_metric_rows
                    if str(row.get("location") or "").strip()
                }
            ),
            "live_bucket": normalized_bucket,
            "live_mrr_sort": normalized_mrr_sort,
            "page_metrics": page_metrics,
            "page": 1,
            "has_prev": False,
            "has_next": has_next,
            "rep_options": rep_options,
            "enterprise_mrr_threshold": ENTERPRISE_MRR_THRESHOLD,
            "outreach_channel_targets": outreach_targets,
        },
    )


@customer_retention_router.get("/customer-retention", response_class=HTMLResponse)
def customer_retention_tracker(
    request: Request,
    db: Session = Depends(get_db),
    due_soon_days: int = Query(7, ge=1, le=30),
    high_balance_only: bool = Query(False),
    segment: str | None = Query(None),
    segments: list[str] = Query(default=[]),
    days_past_due: str | None = Query(None),
    search: str | None = Query(None),
):
    user = get_current_user(request)
    query_segments = request.query_params.getlist("segments")
    query_segment = request.query_params.get("segment")
    query_days_past_due = request.query_params.get("days_past_due")
    selected_segments = _normalize_segment_filters(
        query_segments if query_segments else segments,
        query_segment or segment,
    )
    search_text = (search.strip() if isinstance(search, str) else "") or None
    tracker_customer_ids: list[str] = []
    if hasattr(db, "execute"):
        tracker_customer_ids = _retention_active_customer_ids(db)
        if search_text:
            tracker_customer_ids = list(
                dict.fromkeys(tracker_customer_ids + _retention_search_customer_ids(db, search_text))
            )
    if tracker_customer_ids:
        churn_rows = _retention_billing_rows_for_customer_ids(
            db,
            customer_ids=tracker_customer_ids,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            segment=segment,
            selected_segments=selected_segments,
            days_past_due=query_days_past_due or days_past_due,
            search=search_text,
            limit=6000,
        )
    elif settings.customer_retention_route_use_cache and _billing_risk_cache_available(db):
        churn_rows = billing_risk_cache.all_cached_rows(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            selected_segments=selected_segments,
            days_past_due=query_days_past_due or days_past_due,
            search=search_text,
            limit=6000,
        )
    else:
        churn_rows = billing_risk_service.get_billing_risk_table(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            segment=segment,
            segments=selected_segments,
            days_past_due=query_days_past_due or days_past_due,
            limit=6000,
            enrich_visible_rows=False,
        )
    tracker_rows = _retention_tracker_rows(churn_rows, limit=6000)
    tracker_customer_ids = [_retention_customer_id(row) for row in tracker_rows]
    engagement_history = _retention_engagements_by_customer(db, tracker_customer_ids)
    tracker_rows = [row for row in tracker_rows if engagement_history.get(_retention_customer_id(row))]
    raw_search = request.query_params.get("search")
    search_term = (
        raw_search.strip() if isinstance(raw_search, str) else (search.strip() if isinstance(search, str) else "")
    )
    if search_term:
        matched_customer_ids = _retention_search_customer_ids(db, search_term)
        engagement_history.update(_retention_engagements_by_customer(db, matched_customer_ids))
        search_casefold = search_term.casefold()
        filtered_rows: list[dict] = []
        for row in tracker_rows:
            customer_id = _retention_customer_id(row)
            latest_engagements = engagement_history.get(customer_id) or []
            haystacks = [
                str(row.get("name") or ""),
                str(row.get("phone") or ""),
                str(row.get("email") or ""),
                customer_id,
            ]
            if latest_engagements:
                latest = latest_engagements[0]
                haystacks.extend(
                    [
                        str(latest.get("outcome") or ""),
                        str(latest.get("note") or ""),
                        str(latest.get("rep") or ""),
                    ]
                )
            if any(search_casefold in value.casefold() for value in haystacks if value):
                filtered_rows.append(row)
        tracker_rows = filtered_rows
    tracker_rows = _retention_rows_with_pipeline(tracker_rows, engagement_history)
    tracker_rows = _filter_excluded_retention_rows(tracker_rows)
    follow_up_reminders = _retention_follow_up_reminders(tracker_rows, engagement_history)
    segment_breakdown = billing_risk_service.get_billing_risk_segment_breakdown(tracker_rows)
    filter_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "high_balance_only": str(high_balance_only).lower(),
            "segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
            "search": search_term,
        },
        doseq=True,
    )
    clear_search_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "high_balance_only": str(high_balance_only).lower(),
            "segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
        },
        doseq=True,
    )

    return templates.TemplateResponse(
        "admin/reports/customer_retention_tracker.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "customer-retention",
            "active_menu": "reports",
            "kpis": _retention_tracker_kpis(tracker_rows),
            "rep_options": _retention_rep_options(db),
            "tracker_rows": tracker_rows,
            "engagement_history": engagement_history,
            "follow_up_reminders": follow_up_reminders,
            "pipeline_steps": RETENTION_PIPELINE_STEPS,
            "segment_breakdown": segment_breakdown,
            "due_soon_days": due_soon_days,
            "high_balance_only": high_balance_only,
            "selected_segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
            "search": search_term,
            "filter_query": filter_query,
            "clear_search_query": clear_search_query,
            "last_synced_at": _latest_subscriber_sync_at(db),
            "outreach_channel_targets": outreach_channel_target_options(db),
            "outreach_error": request.query_params.get("outreach_error"),
            "customer_retention_route_mode": "cache"
            if settings.customer_retention_route_use_cache and _billing_risk_cache_available(db)
            else "live",
        },
    )


@customer_retention_router.get("/customer-retention/engagements")
def customer_retention_engagements(
    request: Request,
    db: Session = Depends(get_db),
    customer_id: list[str] = Query(default=[]),
):
    get_current_user(request)
    return JSONResponse({"engagements": _retention_engagements_by_customer(db, customer_id)})


@customer_retention_router.post("/customer-retention/engagements")
async def customer_retention_engagement_create(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    payload = await request.json()
    customer_id = str(payload.get("customerId") or "").strip()
    outcome = str(payload.get("outcome") or "").strip()
    if not customer_id or not outcome:
        raise HTTPException(status_code=400, detail="Customer and outcome are required")

    rep_person_id_raw = str(payload.get("repPersonId") or "").strip()
    rep_label = str(payload.get("rep") or "").strip() or None
    rep_person_id = _optional_uuid(rep_person_id_raw)
    if rep_person_id is not None:
        rep = db.get(Person, rep_person_id)
        if rep is not None:
            rep_label = (
                str(
                    rep.display_name or f"{rep.first_name or ''} {rep.last_name or ''}".strip() or rep.email or ""
                ).strip()
                or rep_label
            )

    engagement = CustomerRetentionEngagement(
        customer_external_id=customer_id,
        customer_name=str(payload.get("customerName") or "").strip() or None,
        outcome=outcome,
        note=str(payload.get("note") or "").strip() or None,
        follow_up_date=_parse_follow_up_date(payload.get("followUp")),
        rep_person_id=rep_person_id,
        rep_label=rep_label,
        created_by_person_id=_optional_uuid(_person_id_from_user(user)),
        is_active=True,
    )
    db.add(engagement)
    db.commit()
    db.refresh(engagement)
    return JSONResponse({"engagement": _retention_engagement_payload(engagement)})


@customer_retention_router.post("/customer-retention/{customer_id}/engagements")
def customer_retention_profile_engagement_create(
    customer_id: str,
    request: Request,
    db: Session = Depends(get_db),
    customer_name: str | None = Form(default=None),
    outcome: str = Form(...),
    note: str | None = Form(default=None),
    follow_up: str | None = Form(default=None),
    rep_person_id: str | None = Form(default=None),
    rep: str | None = Form(default=None),
    due_soon_days: int = Form(7),
):
    user = get_current_user(request)
    normalized_customer_id = str(customer_id or "").strip()
    normalized_outcome = str(outcome or "").strip()
    if not normalized_customer_id or not normalized_outcome:
        raise HTTPException(status_code=400, detail="Customer and outcome are required")

    rep_label = str(rep or "").strip() or None
    rep_person_id_value = _optional_uuid(rep_person_id)
    if rep_person_id_value is not None:
        rep_person = db.get(Person, rep_person_id_value)
        if rep_person is not None:
            rep_label = (
                str(
                    rep_person.display_name
                    or f"{rep_person.first_name or ''} {rep_person.last_name or ''}".strip()
                    or rep_person.email
                    or ""
                ).strip()
                or rep_label
            )

    engagement = CustomerRetentionEngagement(
        customer_external_id=normalized_customer_id,
        customer_name=str(customer_name or "").strip() or None,
        outcome=normalized_outcome,
        note=str(note or "").strip() or None,
        follow_up_date=_parse_follow_up_date(follow_up),
        rep_person_id=rep_person_id_value,
        rep_label=rep_label,
        created_by_person_id=_optional_uuid(_person_id_from_user(user)),
        is_active=True,
    )
    db.add(engagement)
    db.commit()

    return RedirectResponse(
        url=f"/admin/customer-retention/{customer_id}?due_soon_days={due_soon_days}&saved=1",
        status_code=303,
    )


@customer_retention_router.get("/customer-retention/{customer_id}", response_class=HTMLResponse)
def customer_retention_tracker_detail(
    customer_id: str,
    request: Request,
    db: Session = Depends(get_db),
    due_soon_days: int = Query(7, ge=1, le=30),
):
    user = get_current_user(request)
    normalized_customer_id = str(customer_id or "").strip()
    if settings.customer_retention_route_use_cache and _billing_risk_cache_available(db):
        customer = billing_risk_cache.cached_row_by_external_id(
            db,
            normalized_customer_id,
            due_soon_days=due_soon_days,
        )
    else:
        customer_rows = _retention_billing_rows_for_customer_ids(
            db,
            customer_ids=[normalized_customer_id],
            due_soon_days=due_soon_days,
            limit=1,
        )
        customer = customer_rows[0] if customer_rows else None
    if customer is not None:
        visible_customer = dict(customer)
        billing_risk_service.enrich_billing_risk_rows([visible_customer])
    else:
        visible_customer = {
            "name": "Unknown customer",
            "_external_id": normalized_customer_id,
            "plan": "",
            "mrr_total": 0,
            "balance": 0,
            "risk_segment": "",
            "subscriber_status": "",
            "blocked_for_days": None,
        }

    engagement_history = _retention_engagements_by_customer(db, [normalized_customer_id]).get(
        normalized_customer_id, []
    )
    latest_engagement = engagement_history[0] if engagement_history else None
    pipeline_stage = _pipeline_stage_from_engagement(latest_engagement)
    follow_up_due_label = ""
    if latest_engagement:
        days_until_follow_up = _days_until_follow_up(str(latest_engagement.get("followUp") or ""))
        if days_until_follow_up is not None:
            if days_until_follow_up < 0:
                follow_up_due_label = f"{abs(days_until_follow_up)} days overdue"
            elif days_until_follow_up == 0:
                follow_up_due_label = "Due today"
            else:
                follow_up_due_label = f"Due in {days_until_follow_up} days"

    return templates.TemplateResponse(
        "admin/reports/customer_retention_profile.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "customer-retention",
            "active_menu": "reports",
            "customer": visible_customer,
            "customer_id": normalized_customer_id,
            "engagement_history": engagement_history,
            "pipeline_steps": RETENTION_PIPELINE_STEPS,
            "pipeline_stage": pipeline_stage,
            "follow_up_due_label": follow_up_due_label,
            "rep_options": _retention_rep_options(db),
            "saved": request.query_params.get("saved"),
            "back_url": "/admin/reports/subscribers/billing-risk",
            "customer_retention_route_mode": "cache"
            if settings.customer_retention_route_use_cache and _billing_risk_cache_available(db)
            else "live",
        },
    )


@customer_retention_router.post("/customer-retention/outreach")
def customer_retention_create_outreach(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form("Retention Outreach"),
    channel: str = Form("whatsapp"),
    channel_target_id: str = Form(""),
    subscriber_id: list[str] = Form(default=[]),
    retention_customer_id: list[str] = Form(default=[]),
    due_soon_days: int = Form(7),
    high_balance_only: bool = Form(False),
    segments: list[str] = Form(default=[]),
    days_past_due: str = Form(""),
    next_url: str = Form("/admin/customer-retention"),
):
    user = get_current_user(request)
    if not next_url.startswith("/admin/customer-retention"):
        next_url = "/admin/customer-retention"

    selected_customer_ids = [str(value).strip() for value in retention_customer_id if str(value).strip()]
    if not selected_customer_ids:
        return RedirectResponse(
            url=_append_query_flag(next_url, "outreach_error", "no_selection"),
            status_code=303,
        )

    selected_segments = _normalize_segment_filters(segments, None)
    churn_rows = _retention_billing_rows_for_customer_ids(
        db,
        customer_ids=selected_customer_ids,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        selected_segments=selected_segments,
        days_past_due=days_past_due or None,
        limit=len(selected_customer_ids),
    )
    tracker_rows = _retention_tracker_rows(churn_rows, limit=6000)
    engagement_history = _retention_engagements_by_customer(db, [_retention_customer_id(row) for row in tracker_rows])
    tracker_rows = [row for row in tracker_rows if engagement_history.get(_retention_customer_id(row))]
    tracker_rows = _retention_rows_with_pipeline(tracker_rows, engagement_history)
    tracker_rows = _filter_excluded_retention_rows(tracker_rows)
    row_by_customer_id = {_retention_customer_id(row): row for row in tracker_rows}

    selected_subscriber_ids: list[str] = []
    filtered_retention_customer_ids: list[str] = []
    for customer_id in selected_customer_ids:
        row = row_by_customer_id.get(customer_id)
        if not row:
            continue
        subscriber_value = str(row.get("subscriber_id") or "").strip()
        if not subscriber_value:
            continue
        selected_subscriber_ids.append(subscriber_value)
        filtered_retention_customer_ids.append(customer_id)

    if not selected_subscriber_ids:
        return RedirectResponse(
            url=_append_query_flag(next_url, "outreach_error", "No valid subscribers in selection"),
            status_code=303,
        )

    try:
        campaign = create_billing_risk_outreach_campaign(
            db,
            name=name,
            channel=channel,
            channel_target_id=channel_target_id,
            subscriber_ids=selected_subscriber_ids,
            retention_customer_ids=filtered_retention_customer_ids,
            created_by_id=_person_id_from_user(user),
            source_filters={
                "retention_queue": True,
                "selected_count": len(selected_subscriber_ids),
                "due_soon_days": due_soon_days,
                "high_balance_only": bool(high_balance_only),
                "days_past_due": days_past_due or None,
                "query": str(request.url),
            },
        )
    except HTTPException as exc:
        return RedirectResponse(
            url=_append_query_flag(next_url, "outreach_error", str(exc.detail)),
            status_code=303,
        )
    except Exception:
        logger.exception("Failed to create billing risk outreach campaign")
        return RedirectResponse(
            url=_append_query_flag(next_url, "outreach_error", "Unable to create outreach draft"),
            status_code=303,
        )

    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign.id}", status_code=303)


@router.post("/subscribers/billing-risk/refresh")
def subscriber_billing_risk_refresh(
    request: Request,
    next_url: str = Form("/admin/reports/subscribers/billing-risk"),
    _admin: dict = Depends(require_web_role("admin")),
):
    if not next_url.startswith("/admin/reports/subscribers/billing-risk"):
        next_url = "/admin/reports/subscribers/billing-risk"

    try:
        sync_subscribers_from_splynx.delay()
        return RedirectResponse(url=_append_query_flag(next_url, "refresh_started", "1"), status_code=303)
    except Exception:
        logger.exception("Failed to enqueue Splynx subscriber sync")
        return RedirectResponse(url=_append_query_flag(next_url, "refresh_error", "queue_unavailable"), status_code=303)


@router.post("/subscribers/billing-risk/outreach")
def subscriber_billing_risk_create_outreach(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form("Billing Risk Outreach"),
    channel: str = Form("whatsapp"),
    channel_target_id: str = Form(""),
    subscriber_id: list[str] = Form(default=[]),
    retention_customer_id: list[str] = Form(default=[]),
    next_url: str = Form("/admin/reports/subscribers/billing-risk"),
):
    user = get_current_user(request)
    if not next_url.startswith("/admin/reports/subscribers/billing-risk") or next_url.startswith(
        "/admin/reports/subscribers/billing-risk/rows"
    ):
        next_url = "/admin/reports/subscribers/billing-risk"

    selected_subscriber_ids = _resolve_subscriber_ids(
        db, [str(value).strip() for value in subscriber_id if str(value).strip()]
    )
    if not selected_subscriber_ids:
        return RedirectResponse(
            url=_append_query_flag(next_url, "outreach_error", "no_selection"),
            status_code=303,
        )

    try:
        campaign = create_billing_risk_outreach_campaign(
            db,
            name=name,
            channel=channel,
            channel_target_id=channel_target_id,
            subscriber_ids=selected_subscriber_ids,
            retention_customer_ids=retention_customer_id,
            created_by_id=_person_id_from_user(user),
            source_filters={
                "query": request.headers.get("referer", ""),
                "selected_count": len(selected_subscriber_ids),
            },
        )
    except HTTPException as exc:
        return RedirectResponse(
            url=_append_query_flag(next_url, "outreach_error", str(exc.detail)),
            status_code=303,
        )

    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign.id}", status_code=303)


@router.get("/subscribers/billing-risk/blocked-dates")
def subscriber_billing_risk_blocked_dates(
    request: Request,
    external_id: list[str] = Query(default=[]),
    blocked_like_external_id: list[str] = Query(default=[]),
):
    get_current_user(request)
    blocked_dates = _safe_live_blocked_dates(
        external_id,
        force_live=False,
        blocking_only_external_ids=blocked_like_external_id,
    )
    return JSONResponse({"blocked_dates": blocked_dates})


@router.get("/subscribers/billing-risk/rows", response_class=HTMLResponse)
def subscriber_billing_risk_rows(
    request: Request,
    db: Session = Depends(get_db),
    due_soon_days: int = Query(7, ge=1, le=30),
    overdue_invoice_days: int = Query(30, ge=1, le=180),
    high_balance_only: bool = Query(False),
    segment: str | None = Query(None),
    segments: list[str] = Query(default=[]),
    days_past_due: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    search: str | None = Query(None),
    bucket: str | None = Query("all"),
    enterprise_only: bool = Query(False),
    customer_segment: str | None = Query(None),
    location: str | None = Query(None),
    mrr_sort: str | None = Query(None),
):
    get_current_user(request)
    query_segments = request.query_params.getlist("segments")
    query_segment = request.query_params.get("segment")
    query_days_past_due = request.query_params.get("days_past_due")
    query_search = request.query_params.get("search")
    normalized_search = query_search if query_search is not None else (search if isinstance(search, str) else None)
    query_bucket = request.query_params.get("bucket")
    normalized_bucket = (
        query_bucket if query_bucket is not None else (bucket if isinstance(bucket, str) else "all")
    ).strip() or "all"
    normalized_customer_segment = "all"
    normalized_enterprise_only = False
    normalized_location = (
        request.query_params.get("location") or (location if isinstance(location, str) else "")
    ).strip()
    query_mrr_sort = request.query_params.get("mrr_sort")
    normalized_mrr_sort = (
        (query_mrr_sort if query_mrr_sort is not None else (mrr_sort if isinstance(mrr_sort, str) else ""))
        .strip()
        .lower()
    )
    selected_segments = _normalize_segment_filters(
        query_segments if query_segments else segments,
        query_segment or segment,
    )
    if (
        settings.billing_risk_route_use_cache
        and _billing_risk_cache_available(db)
        and not normalized_enterprise_only
        and normalized_customer_segment == "all"
    ):
        page_rows, page_metrics, has_next = _billing_risk_cached_page_rows(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            selected_segments=selected_segments,
            days_past_due=query_days_past_due or days_past_due,
            page=page,
            page_size=page_size,
            search=normalized_search,
            overdue_bucket=normalized_bucket,
            location=normalized_location,
        )
    else:
        page_rows, page_metrics, has_next = _billing_risk_page_rows(
            db,
            due_soon_days=due_soon_days,
            high_balance_only=high_balance_only,
            segment=segment,
            selected_segments=selected_segments,
            days_past_due=query_days_past_due or days_past_due,
            page=page,
            page_size=page_size,
            search=normalized_search,
            overdue_bucket=normalized_bucket,
            enterprise_only=normalized_enterprise_only,
            customer_segment=normalized_customer_segment,
            location=normalized_location,
            mrr_sort=normalized_mrr_sort,
        )
    return templates.TemplateResponse(
        "admin/reports/_subscriber_billing_risk_results.html",
        {
            "request": request,
            "churn_rows": page_rows,
            "page_metrics": page_metrics,
            "page": page,
            "page_size": page_size,
            "has_prev": page > 1,
            "has_next": has_next,
            "enterprise_mrr_threshold": ENTERPRISE_MRR_THRESHOLD,
            "outreach_channel_targets": outreach_channel_target_options(db),
            "csrf_token": get_csrf_token(request),
        },
    )


@router.get("/subscribers/billing-risk/blocked-date-cell", response_class=HTMLResponse)
def subscriber_billing_risk_blocked_date_cell(
    request: Request,
    external_id: str = Query(...),
):
    get_current_user(request)
    blocked_dates = _safe_live_blocked_dates([external_id], force_live=False)
    return HTMLResponse(blocked_dates.get(external_id, "N/A"))


@router.get("/subscribers/billing-risk/export")
def subscriber_billing_risk_export(
    request: Request,
    db: Session = Depends(get_db),
    due_soon_days: int = Query(7, ge=1, le=30),
    high_balance_only: bool = Query(False),
    segment: str | None = Query(None),
    segments: list[str] = Query(default=[]),
    days_past_due: str | None = Query(None),
    search: str | None = Query(None),
    bucket: str | None = Query("all"),
    enterprise_only: bool = Query(False),
    customer_segment: str | None = Query(None),
    location: str | None = Query(None),
    mrr_sort: str | None = Query(None),
):
    query_segments = request.query_params.getlist("segments")
    query_segment = request.query_params.get("segment")
    query_days_past_due = request.query_params.get("days_past_due")
    query_search = request.query_params.get("search")
    normalized_search = query_search if query_search is not None else (search if isinstance(search, str) else None)
    query_location = request.query_params.get("location")
    normalized_location = (
        query_location if query_location is not None else (location if isinstance(location, str) else "")
    )
    normalized_location = normalized_location.strip()
    query_bucket = request.query_params.get("bucket")
    normalized_bucket = (
        query_bucket if query_bucket is not None else (bucket if isinstance(bucket, str) else "all")
    ).strip() or "all"
    normalized_customer_segment = "all"
    normalized_enterprise_only = False
    query_mrr_sort = request.query_params.get("mrr_sort")
    normalized_mrr_sort = (
        (query_mrr_sort if query_mrr_sort is not None else (mrr_sort if isinstance(mrr_sort, str) else ""))
        .strip()
        .lower()
    )
    selected_segments = _normalize_segment_filters(
        query_segments if query_segments else segments,
        query_segment or segment,
    )

    churn_rows, _route_state = _billing_risk_rows_source(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        segment=segment,
        selected_segments=selected_segments,
        days_past_due=query_days_past_due or days_past_due,
        search=normalized_search,
        overdue_bucket=normalized_bucket,
        enterprise_only=normalized_enterprise_only,
        customer_segment=normalized_customer_segment,
        location=normalized_location,
        mrr_sort=normalized_mrr_sort,
        limit=6000,
    )
    selected_labels = _segment_labels(selected_segments)
    if selected_labels:
        churn_rows = [row for row in churn_rows if str(row.get("risk_segment") or "") in selected_labels]
    _enrich_missing_blocked_fields(churn_rows, force_live=False)
    export_data = _billing_risk_visible_export_rows(db, churn_rows)
    filename = f"subscriber_billing_risk_{datetime.now(UTC).strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)
