"""Dedicated Billing Risk admin routes."""

from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, date, datetime
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.csrf import get_csrf_token
from app.db import get_db
from app.models.crm.team import CrmAgent, CrmAgentTeam, CrmTeam
from app.models.customer_retention import CustomerRetentionEngagement
from app.models.person import Person
from app.models.service_team import ServiceTeam, ServiceTeamMember
from app.models.subscriber import Subscriber, SubscriberStatus
from app.services import billing_risk_cache as billing_risk_cache_service
from app.services import billing_risk_reports as billing_risk_service
from app.services.auth_dependencies import require_any_permission
from app.services.common import coerce_uuid
from app.tasks.subscribers import refresh_billing_risk_cache
from app.web.admin._auth_helpers import get_current_user, get_sidebar_stats
from app.web.templates import Jinja2Templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["admin-reports"])
customer_retention_router = APIRouter(tags=["admin-customer-retention"])
templates = Jinja2Templates(directory="templates")

RETENTION_PIPELINE_STEPS = ("Contacted", "Follow-up Pending", "Promised to Pay", "Resolved", "Lost")
RETENTION_REP_LABEL_ONLY_NAMES = {"ejiro onovwiona"}
RETENTION_TARGET_REP_TEAM_NAMES = {
    "customer support",
    "customer-support",
    "customer_support",
    "customer support team",
    "enterprise sales",
    "enterprise-sales",
    "enterprise_sales",
    "sales call center",
    "sales-call-center",
    "sales_call_center",
}
RETENTION_TARGET_REP_DEPARTMENT_NAMES = {
    "customer support",
    "customer_support",
}
RETENTION_TARGET_REP_NAME_FRAGMENTS = (("customer", "support"), ("sales", "call", "center"), ("help", "desk"))
RETENTION_FIXED_REP_LABELS = (
    "Abigail Tongov",
    "Chizaram Ogbonna",
    "Grace Moses",
    "Stephanie Mojekwu",
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
) -> tuple[list[dict], dict[str, int | float], bool]:
    fetch_size = max(1, int(page_size)) + 1
    churn_rows = billing_risk_service.get_billing_risk_table(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        segment=segment,
        segments=selected_segments,
        days_past_due=days_past_due,
        page=page,
        page_size=fetch_size,
        search=search,
        overdue_bucket=overdue_bucket,
        enrich_visible_rows=False,
    )
    selected_labels = _segment_labels(selected_segments)
    if selected_labels:
        churn_rows = [row for row in churn_rows if str(row.get("risk_segment") or "") in selected_labels]
    has_next = len(churn_rows) > page_size
    visible_rows = churn_rows[:page_size]
    if not str(search or "").strip():
        billing_risk_service.enrich_billing_risk_rows(visible_rows)
    return visible_rows, _billing_risk_page_metrics(visible_rows), has_next


def _billing_risk_initial_rows(
    churn_rows: list[dict],
    *,
    page_size: int,
) -> tuple[list[dict], dict[str, int | float], bool]:
    has_next = len(churn_rows) > page_size
    visible_rows = [dict(row) for row in churn_rows[:page_size]]
    billing_risk_service.enrich_billing_risk_rows(visible_rows)
    return visible_rows, _billing_risk_page_metrics(visible_rows), has_next


def _retention_rep_options(db: Session) -> list[dict[str, str]]:
    def _team_is_target(team_name: str | None) -> bool:
        normalized = str(team_name or "").strip().lower()
        if not normalized:
            return False
        normalized_spaced = normalized.replace("_", " ").replace("-", " ").strip()
        normalized_spaced = " ".join(normalized_spaced.split())
        if normalized_spaced in RETENTION_TARGET_REP_TEAM_NAMES:
            return True

        normalized_underscored = normalized_spaced.replace(" ", "_")
        if normalized_underscored in RETENTION_TARGET_REP_DEPARTMENT_NAMES:
            return True

        return any(
            all(fragment in normalized_spaced for fragment in fragments)
            for fragments in RETENTION_TARGET_REP_NAME_FRAGMENTS
        )

    options_by_person_id: dict[str, dict[str, str]] = {}
    fixed_options_by_label: dict[str, dict[str, str]] = {}

    service_team_rows = db.execute(
        select(
            Person.id,
            Person.display_name,
            Person.first_name,
            Person.last_name,
            Person.email,
            ServiceTeam.name,
            ServiceTeam.erp_department,
        )
        .select_from(ServiceTeamMember)
        .join(ServiceTeam, ServiceTeam.id == ServiceTeamMember.team_id)
        .join(Person, Person.id == ServiceTeamMember.person_id)
        .where(
            ServiceTeam.is_active.is_(True),
            ServiceTeamMember.is_active.is_(True),
            Person.is_active.is_(True),
        )
    ).all()
    for service_row in service_team_rows:
        (
            person_id,
            display_name,
            first_name,
            last_name,
            email,
            team_name,
            *service_team_tail,
        ) = service_row
        team_department = service_team_tail[0] if service_team_tail else None
        if not (_team_is_target(team_name) or _team_is_target(team_department)):
            continue
        label = str(display_name or f"{first_name or ''} {last_name or ''}".strip() or email or "Unnamed rep").strip()
        team_label = "" if label.casefold() in RETENTION_REP_LABEL_ONLY_NAMES else str(team_name or "").strip()
        options_by_person_id[str(person_id)] = {
            "value": str(person_id),
            "label": label,
            "team": team_label,
            "person_id": str(person_id),
        }

    crm_team_rows = db.execute(
        select(
            Person.id,
            Person.display_name,
            Person.first_name,
            Person.last_name,
            Person.email,
            CrmTeam.name,
            ServiceTeam.name,
            ServiceTeam.erp_department,
        )
        .select_from(CrmAgentTeam)
        .join(CrmTeam, CrmTeam.id == CrmAgentTeam.team_id)
        .outerjoin(ServiceTeam, ServiceTeam.id == CrmTeam.service_team_id)
        .join(CrmAgent, CrmAgent.id == CrmAgentTeam.agent_id)
        .join(Person, Person.id == CrmAgent.person_id)
        .where(
            CrmTeam.is_active.is_(True),
            CrmAgentTeam.is_active.is_(True),
            CrmAgent.is_active.is_(True),
            Person.is_active.is_(True),
        )
    ).all()
    for crm_row in crm_team_rows:
        (
            person_id,
            display_name,
            first_name,
            last_name,
            email,
            team_name,
            *crm_team_tail,
        ) = crm_row
        crm_service_team_name = crm_team_tail[0] if len(crm_team_tail) >= 1 else None
        crm_service_department = crm_team_tail[1] if len(crm_team_tail) >= 2 else None
        if not (
            _team_is_target(team_name)
            or _team_is_target(crm_service_team_name)
            or _team_is_target(crm_service_department)
        ):
            continue
        label = str(display_name or f"{first_name or ''} {last_name or ''}".strip() or email or "Unnamed rep").strip()
        team_label = "" if label.casefold() in RETENTION_REP_LABEL_ONLY_NAMES else str(team_name or "").strip()
        options_by_person_id.setdefault(
            str(person_id),
            {
                "value": str(person_id),
                "label": label,
                "team": team_label,
                "person_id": str(person_id),
            },
        )

    for option in options_by_person_id.values():
        fixed_options_by_label[option["label"].casefold()] = option
    for label in RETENTION_FIXED_REP_LABELS:
        fixed_options_by_label.setdefault(
            label.casefold(),
            {
                "value": label,
                "label": label,
                "team": "",
                "person_id": "",
            },
        )

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


def _pipeline_stage_from_engagement(engagement: dict[str, str | None] | None) -> str:
    if not engagement:
        return "Contacted"
    outcome = str(engagement.get("outcome") or "").strip()
    follow_up = str(engagement.get("followUp") or "").strip()
    if outcome == "Renewing":
        return "Resolved"
    if outcome == "Churning":
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
        candidate["pipeline_stage"] = _pipeline_stage_from_engagement(latest_engagement)
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
        stage = _pipeline_stage_from_engagement(latest_engagement)
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


def _retention_tracker_kpis(db: Session, churn_rows: list[dict]) -> dict[str, int | float]:
    recovery_segments = {"Suspended", "Due Soon"}
    tracked_count = len(churn_rows)
    recovery_priority_count = sum(1 for row in churn_rows if str(row.get("risk_segment") or "") in recovery_segments)
    due_soon_count = sum(1 for row in churn_rows if str(row.get("risk_segment") or "") in {"Due Soon", "Pending"})
    churned_count = sum(1 for row in churn_rows if str(row.get("risk_segment") or "") == "Churned")
    won_back_count = (
        db.scalar(
            select(func.count(Subscriber.id)).where(
                Subscriber.external_system == "splynx",
                Subscriber.status == SubscriberStatus.active,
                Subscriber.terminated_at.isnot(None),
                Subscriber.is_active.is_(True),
            )
        )
        or 0
    )
    winback_pool_count = int(churned_count) + int(won_back_count)
    winback_rate = round((int(won_back_count) / winback_pool_count) * 100, 1) if winback_pool_count else 0.0
    revenue_at_risk = round(sum(float(row.get("balance") or 0) for row in churn_rows), 2)
    high_balance_count = sum(1 for row in churn_rows if bool(row.get("is_high_balance_risk")))
    return {
        "tracked_count": tracked_count,
        "recovery_priority_count": recovery_priority_count,
        "due_soon_count": due_soon_count,
        "winback_rate": winback_rate,
        "won_back_count": int(won_back_count),
        "churned_count": churned_count,
        "revenue_at_risk": revenue_at_risk,
        "high_balance_count": high_balance_count,
    }


@router.get(
    "/subscribers/billing-risk",
    response_class=HTMLResponse,
    dependencies=[Depends(require_any_permission("reports:billing", "reports:subscribers", "reports"))],
)
def subscriber_billing_risk(
    request: Request,
    db: Session = Depends(get_db),
    due_soon_days: int = Query(7, ge=1, le=30),
    overdue_invoice_days: int = Query(30, ge=1, le=180),
    high_balance_only: bool = Query(False),
    segment: str | None = Query(None),
    segments: list[str] = Query(default=[]),
    days_past_due: str | None = Query(None),
    page_size: int = Query(50, ge=1, le=100),
):
    user = get_current_user(request)
    query_segments = request.query_params.getlist("segments")
    query_segment = request.query_params.get("segment")
    query_days_past_due = request.query_params.get("days_past_due")
    selected_segments = _normalize_segment_filters(
        query_segments if query_segments else segments,
        query_segment or segment,
    )
    global_churn_rows = billing_risk_cache_service.all_cached_rows(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        selected_segments=selected_segments,
        days_past_due=query_days_past_due or days_past_due,
        limit=10000,
    )
    page = billing_risk_cache_service.list_cached_rows(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        selected_segments=selected_segments,
        days_past_due=query_days_past_due or days_past_due,
        page=1,
        page_size=page_size,
    )
    overdue_invoices = billing_risk_service.get_overdue_invoices_table(
        db,
        min_days_past_due=overdue_invoice_days,
        limit=250,
    )
    kpis = billing_risk_cache_service.summary(global_churn_rows, overdue_invoices)
    segment_breakdown = billing_risk_cache_service.segment_breakdown(global_churn_rows)
    aging_buckets = billing_risk_cache_service.aging_buckets(global_churn_rows)
    cache_meta = billing_risk_cache_service.cache_metadata(db)

    export_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
        },
        doseq=True,
    )
    retention_tracker_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "high_balance_only": str(high_balance_only).lower(),
            "segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
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
        },
        doseq=True,
    )

    return templates.TemplateResponse(
        "admin/reports/subscriber_billing_risk.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-billing-risk",
            "active_menu": "reports",
            "kpis": kpis,
            "segment_breakdown": segment_breakdown,
            "aging_buckets": aging_buckets,
            "churn_rows": page.rows,
            "overdue_invoices": overdue_invoices,
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": high_balance_only,
            "selected_segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
            "export_query": export_query,
            "retention_tracker_query": retention_tracker_query,
            "refresh_query": refresh_query,
            "last_synced_at": cache_meta.get("refreshed_at") or _latest_subscriber_sync_at(db),
            "billing_risk_cache": cache_meta,
            "csrf_token": get_csrf_token(request),
            "refresh_started": request.query_params.get("refresh_started") == "1",
            "refresh_error": request.query_params.get("refresh_error"),
            "live_page": 1,
            "live_page_size": page_size,
            "live_has_next": page.has_next,
            "live_search": "",
            "live_bucket": "all",
            "page_metrics": page.page_metrics,
            "page": page.page,
            "page_size": page_size,
            "total_pages": page.total_pages,
            "total_count": page.total_count,
            "has_prev": False,
            "has_next": page.has_next,
            "rep_options": _retention_rep_options(db),
        },
    )


@customer_retention_router.get(
    "/customer-retention",
    response_class=HTMLResponse,
    dependencies=[Depends(require_any_permission("reports:billing", "reports:subscribers", "reports"))],
)
def customer_retention_tracker(
    request: Request,
    db: Session = Depends(get_db),
    due_soon_days: int = Query(7, ge=1, le=30),
    high_balance_only: bool = Query(False),
    segment: str | None = Query(None),
    segments: list[str] = Query(default=[]),
    days_past_due: str | None = Query(None),
):
    user = get_current_user(request)
    query_segments = request.query_params.getlist("segments")
    query_segment = request.query_params.get("segment")
    query_days_past_due = request.query_params.get("days_past_due")
    selected_segments = _normalize_segment_filters(
        query_segments if query_segments else segments,
        query_segment or segment,
    )
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
    tracker_rows = _retention_rows_with_pipeline(tracker_rows, engagement_history)
    follow_up_reminders = _retention_follow_up_reminders(tracker_rows, engagement_history)
    segment_breakdown = billing_risk_service.get_billing_risk_segment_breakdown(tracker_rows)
    filter_query = urlencode(
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
            "kpis": _retention_tracker_kpis(db, tracker_rows),
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
            "filter_query": filter_query,
            "last_synced_at": _latest_subscriber_sync_at(db),
        },
    )


@customer_retention_router.get(
    "/customer-retention/engagements",
    dependencies=[Depends(require_any_permission("reports:billing", "reports:subscribers", "reports"))],
)
def customer_retention_engagements(
    request: Request,
    db: Session = Depends(get_db),
    customer_id: list[str] = Query(default=[]),
):
    get_current_user(request)
    return JSONResponse({"engagements": _retention_engagements_by_customer(db, customer_id)})


@customer_retention_router.post(
    "/customer-retention/engagements",
    dependencies=[Depends(require_any_permission("reports:billing", "reports:subscribers", "reports"))],
)
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


@customer_retention_router.get(
    "/customer-retention/{customer_id}",
    response_class=HTMLResponse,
    dependencies=[Depends(require_any_permission("reports:billing", "reports:subscribers", "reports"))],
)
def customer_retention_tracker_detail(
    customer_id: str,
    request: Request,
    db: Session = Depends(get_db),
    due_soon_days: int = Query(7, ge=1, le=30),
):
    user = get_current_user(request)
    churn_rows = billing_risk_service.get_billing_risk_table(
        db,
        due_soon_days=due_soon_days,
        limit=6000,
        enrich_visible_rows=False,
    )
    customer = next(
        (row for row in churn_rows if _retention_customer_id(row) == str(customer_id)),
        None,
    )
    if customer is not None:
        visible_customer = dict(customer)
        billing_risk_service.enrich_billing_risk_rows([visible_customer])
    else:
        visible_customer = {
            "name": "Unknown customer",
            "_external_id": customer_id,
            "plan": "",
            "mrr_total": 0,
            "balance": 0,
            "risk_segment": "",
            "subscriber_status": "",
            "blocked_for_days": None,
        }

    engagement_history = _retention_engagements_by_customer(db, [customer_id]).get(customer_id, [])
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
            "customer_id": customer_id,
            "engagement_history": engagement_history,
            "pipeline_steps": RETENTION_PIPELINE_STEPS,
            "pipeline_stage": pipeline_stage,
            "follow_up_due_label": follow_up_due_label,
            "rep_options": _retention_rep_options(db),
            "back_url": "/admin/reports/subscribers/billing-risk",
        },
    )


@router.post("/subscribers/billing-risk/refresh")
def subscriber_billing_risk_refresh(
    request: Request,
    next_url: str = Form("/admin/reports/subscribers/billing-risk"),
    _permission: dict = Depends(require_any_permission("reports:billing", "reports:subscribers", "reports")),
):
    if not next_url.startswith("/admin/reports/subscribers/billing-risk"):
        next_url = "/admin/reports/subscribers/billing-risk"

    try:
        refresh_billing_risk_cache.delay()
        return RedirectResponse(url=_append_query_flag(next_url, "refresh_started", "1"), status_code=303)
    except Exception:
        logger.exception("Failed to enqueue billing risk cache refresh")
        return RedirectResponse(url=_append_query_flag(next_url, "refresh_error", "queue_unavailable"), status_code=303)


@router.get(
    "/subscribers/billing-risk/blocked-dates",
    dependencies=[Depends(require_any_permission("reports:billing", "reports:subscribers", "reports"))],
)
def subscriber_billing_risk_blocked_dates(
    request: Request,
    external_id: list[str] = Query(default=[]),
):
    get_current_user(request)
    blocked_dates = billing_risk_service.get_live_blocked_dates(external_id)
    return JSONResponse({"blocked_dates": blocked_dates})


@router.get(
    "/subscribers/billing-risk/rows",
    response_class=HTMLResponse,
    dependencies=[Depends(require_any_permission("reports:billing", "reports:subscribers", "reports"))],
)
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
):
    get_current_user(request)
    query_segments = request.query_params.getlist("segments")
    query_segment = request.query_params.get("segment")
    query_days_past_due = request.query_params.get("days_past_due")
    selected_segments = _normalize_segment_filters(
        query_segments if query_segments else segments,
        query_segment or segment,
    )
    page_result = billing_risk_cache_service.list_cached_rows(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        selected_segments=selected_segments,
        days_past_due=query_days_past_due or days_past_due,
        page=page,
        page_size=page_size,
        search=search,
        overdue_bucket=bucket,
    )
    return templates.TemplateResponse(
        "admin/reports/_subscriber_billing_risk_results.html",
        {
            "request": request,
            "churn_rows": page_result.rows,
            "page_metrics": page_result.page_metrics,
            "page": page_result.page,
            "page_size": page_size,
            "total_pages": page_result.total_pages,
            "total_count": page_result.total_count,
            "has_prev": page_result.page > 1,
            "has_next": page_result.has_next,
        },
    )


@router.get(
    "/subscribers/billing-risk/blocked-date-cell",
    response_class=HTMLResponse,
    dependencies=[Depends(require_any_permission("reports:billing", "reports:subscribers", "reports"))],
)
def subscriber_billing_risk_blocked_date_cell(
    request: Request,
    external_id: str = Query(...),
):
    get_current_user(request)
    blocked_dates = billing_risk_service.get_live_blocked_dates([external_id])
    return HTMLResponse(blocked_dates.get(external_id, "N/A"))


@router.get(
    "/subscribers/billing-risk/export",
    dependencies=[Depends(require_any_permission("reports:billing", "reports:subscribers", "reports"))],
)
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
):
    query_segments = request.query_params.getlist("segments")
    query_segment = request.query_params.get("segment")
    query_days_past_due = request.query_params.get("days_past_due")
    selected_segments = _normalize_segment_filters(
        query_segments if query_segments else segments,
        query_segment or segment,
    )

    churn_rows = billing_risk_cache_service.all_cached_rows(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        selected_segments=selected_segments,
        days_past_due=query_days_past_due or days_past_due,
        search=search,
        overdue_bucket=bucket,
        limit=6000,
    )
    selected_labels = _segment_labels(selected_segments)
    if selected_labels:
        churn_rows = [row for row in churn_rows if str(row.get("risk_segment") or "") in selected_labels]
    export_data = _billing_risk_visible_export_rows(db, churn_rows)
    filename = f"subscriber_billing_risk_{datetime.now(UTC).strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)
