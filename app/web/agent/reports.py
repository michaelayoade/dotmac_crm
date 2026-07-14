"""Agent performance report routes."""

from datetime import UTC, datetime, timedelta
from typing import TypedDict

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.crm.enums import ChannelType
from app.services.crm import reports as crm_reports_service
from app.services.crm.inbox.agents import get_current_agent_id
from app.services.crm.inbox.permissions import can_view_inbox
from app.web.admin._auth_helpers import get_current_user, get_sidebar_stats
from app.web.auth.dependencies import require_web_auth
from app.web.templates import Jinja2Templates

router = APIRouter(
    prefix="/agent",
    tags=["web-agent"],
    dependencies=[Depends(require_web_auth)],
)
templates = Jinja2Templates(directory="templates")


class _AgentPerformanceDateRange(TypedDict):
    start_at: datetime
    end_at: datetime
    start_date: str
    end_date: str
    error: str | None
    custom_range: bool


def _resolve_date_range(
    *,
    days: int | None,
    start_date: str | None,
    end_date: str | None,
    now: datetime | None = None,
) -> _AgentPerformanceDateRange:
    current = now or datetime.now(UTC)
    resolved_days = days or 30
    start_value = (start_date or "").strip()
    end_value = (end_date or "").strip()

    if start_value and end_value:
        try:
            parsed_start = datetime.fromisoformat(start_value).replace(tzinfo=UTC)
            parsed_end = datetime.fromisoformat(end_value).replace(tzinfo=UTC)
        except ValueError:
            return {
                "start_at": current - timedelta(days=resolved_days),
                "end_at": current,
                "start_date": start_value,
                "end_date": end_value,
                "error": "Enter valid start and end dates.",
                "custom_range": True,
            }
        if parsed_start.date() > parsed_end.date():
            return {
                "start_at": current - timedelta(days=resolved_days),
                "end_at": current,
                "start_date": start_value,
                "end_date": end_value,
                "error": "Start date must be on or before end date.",
                "custom_range": True,
            }
        if parsed_end.date() > current.date():
            return {
                "start_at": current - timedelta(days=resolved_days),
                "end_at": current,
                "start_date": start_value,
                "end_date": end_value,
                "error": "End date cannot be in the future.",
                "custom_range": True,
            }
        start_at = parsed_start.replace(hour=0, minute=0, second=0, microsecond=0)
        end_at = parsed_end.replace(hour=23, minute=59, second=59, microsecond=999999)
        preset_start = current.date() - timedelta(days=resolved_days)
        return {
            "start_at": start_at,
            "end_at": end_at,
            "start_date": parsed_start.date().isoformat(),
            "end_date": parsed_end.date().isoformat(),
            "error": None,
            "custom_range": not (parsed_start.date() == preset_start and parsed_end.date() == current.date()),
        }

    if start_value or end_value:
        return {
            "start_at": current - timedelta(days=resolved_days),
            "end_at": current,
            "start_date": start_value,
            "end_date": end_value,
            "error": "Select both a start date and an end date.",
            "custom_range": True,
        }

    start_at = current - timedelta(days=resolved_days)
    return {
        "start_at": start_at,
        "end_at": current,
        "start_date": start_at.date().isoformat(),
        "end_date": current.date().isoformat(),
        "error": None,
        "custom_range": False,
    }


def _get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _load_my_performance_metrics(
    *,
    db: Session,
    person_id: str | None,
    start_at: datetime,
    end_at: datetime,
    channel_type: str | None,
) -> dict:
    """Load performance metrics for the current agent only."""
    agent_id = get_current_agent_id(db, person_id)

    inbox_stats = {"messages": {"total": 0, "inbound": 0, "outbound": 0}}
    trend_data = []
    agent_stats = []

    if agent_id:
        inbox_stats = crm_reports_service.inbox_kpis(
            db=db,
            start_at=start_at,
            end_at=end_at,
            channel_type=channel_type,
            agent_id=agent_id,
            team_id=None,
        )
        presence_hours = crm_reports_service.agent_presence_summary(
            db=db,
            start_at=start_at,
            end_at=end_at,
            agent_id=agent_id,
        )
        agent_stats = crm_reports_service.agent_performance_metrics(
            db=db,
            start_at=start_at,
            end_at=end_at,
            agent_id=agent_id,
            team_id=None,
            channel_type=channel_type,
        )
        trend_data = crm_reports_service.conversation_trend(
            db=db,
            start_at=start_at,
            end_at=end_at,
            agent_id=agent_id,
            team_id=None,
            channel_type=channel_type,
        )
    else:
        presence_hours = {}

    sales_stats = None
    sales_results = crm_reports_service.agent_sales_performance(
        db=db,
        start_at=start_at,
        end_at=end_at,
        pipeline_id=None,
    )
    if agent_id:
        for row in sales_results:
            if row.get("agent_id") == agent_id:
                sales_stats = row
                break
    if not sales_stats:
        sales_stats = {
            "agent_id": agent_id or "",
            "name": "",
            "deals_won": 0,
            "deals_lost": 0,
            "total_deals": 0,
            "won_value": 0,
            "win_rate": None,
        }

    return {
        "agent_id": agent_id,
        "inbox_stats": inbox_stats,
        "presence_hours": presence_hours,
        "agent_stats": agent_stats,
        "trend_data": trend_data,
        "sales_stats": sales_stats,
    }


def _load_leaderboard_metrics(
    *,
    db: Session,
    start_at: datetime,
    end_at: datetime,
) -> list[dict]:
    """Load public leaderboard metrics for all agents."""
    agent_stats = crm_reports_service.agent_performance_metrics(
        db=db,
        start_at=start_at,
        end_at=end_at,
        agent_id=None,
        team_id=None,
        channel_type=None,
    )
    sales_results = crm_reports_service.agent_sales_performance(
        db=db,
        start_at=start_at,
        end_at=end_at,
        pipeline_id=None,
    )
    sales_map = {row.get("agent_id"): row for row in sales_results}

    leaderboard: list[dict] = []
    for agent in agent_stats:
        agent_id = agent.get("agent_id")
        sales = sales_map.get(agent_id) or {}
        deals_closed = int(sales.get("total_deals") or 0)
        leaderboard.append(
            {
                "agent_id": agent_id,
                "name": agent.get("name") or "Agent",
                "deals_closed": deals_closed,
                "avg_first_response_minutes": agent.get("avg_first_response_minutes"),
            }
        )

    leaderboard.sort(
        key=lambda row: (
            -(row.get("deals_closed") or 0),
            row.get("avg_first_response_minutes") or float("inf"),
            row.get("name") or "",
        )
    )
    return leaderboard


@router.get("/my-performance", response_class=HTMLResponse)
def my_performance(
    request: Request,
    db: Session = Depends(_get_db),
    days: int = Query(30, ge=7, le=90),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    channel_type: str | None = Query(None),
):
    current_user = get_current_user(request)
    roles = current_user.get("roles") or []
    scopes = current_user.get("permissions") or []
    if not can_view_inbox(roles, scopes):
        raise HTTPException(status_code=403, detail="Forbidden")

    now = datetime.now(UTC)
    date_range = _resolve_date_range(days=days, start_date=start_date, end_date=end_date, now=now)
    report_ready = date_range["error"] is None

    channel_value = None
    if channel_type:
        try:
            channel_value = ChannelType(channel_type).value
        except ValueError:
            channel_value = None

    if report_ready:
        metrics = _load_my_performance_metrics(
            db=db,
            person_id=current_user.get("person_id"),
            start_at=date_range["start_at"],
            end_at=date_range["end_at"],
            channel_type=channel_value,
        )
        leaderboard = _load_leaderboard_metrics(
            db=db,
            start_at=date_range["start_at"],
            end_at=date_range["end_at"],
        )
    else:
        metrics = {
            "agent_id": None,
            "inbox_stats": {"messages": {"total": 0, "inbound": 0, "outbound": 0, "by_channel": {}}},
            "presence_hours": {},
            "agent_stats": [],
            "trend_data": [],
            "sales_stats": {"total_deals": 0, "deals_won": 0, "deals_lost": 0, "won_value": 0, "win_rate": None},
        }
        leaderboard = []

    agent_stats = metrics["agent_stats"]
    inbox_stats = metrics["inbox_stats"]
    current_agent_stats = agent_stats[0] if agent_stats else None

    total_conversations = sum(agent["total_conversations"] for agent in agent_stats)
    resolved_conversations = sum(agent["resolved_conversations"] for agent in agent_stats)
    resolution_rate = resolved_conversations / total_conversations * 100 if total_conversations > 0 else 0

    # Weight by conversations with a measured first response, not all conversations.
    total_team_response_minutes = sum(
        (a["avg_first_response_minutes"] or 0) * int(a.get("first_response_count") or 0)
        for a in agent_stats
        if a["avg_first_response_minutes"] is not None
    )
    total_convos_with_frt = sum(
        int(a.get("first_response_count") or 0) for a in agent_stats if a["avg_first_response_minutes"] is not None
    )
    avg_frt = total_team_response_minutes / total_convos_with_frt if total_convos_with_frt > 0 else None

    # Weight by conversations with measured resolution time, not all resolved conversations.
    total_resolution_minutes = sum(
        (a["avg_resolution_minutes"] or 0) * int(a.get("resolution_time_count") or 0)
        for a in agent_stats
        if a["avg_resolution_minutes"] is not None
    )
    total_resolution_samples = sum(
        int(a.get("resolution_time_count") or 0) for a in agent_stats if a["avg_resolution_minutes"] is not None
    )
    avg_resolution_time = total_resolution_minutes / total_resolution_samples if total_resolution_samples > 0 else None

    channel_breakdown = inbox_stats.get("messages", {}).get("by_channel", {})
    channel_labels: dict[str, str] = {}
    email_inbox_breakdown = inbox_stats.get("messages", {}).get("by_email_inbox", {}) or {}

    if email_inbox_breakdown:
        channel_breakdown.pop(str(ChannelType.email), None)
        for inbox_id, data in email_inbox_breakdown.items():
            inbox_key = f"email:{inbox_id}"
            channel_breakdown[inbox_key] = data.get("count", 0)
            inbox_label = data.get("label") or "Unknown Inbox"
            channel_labels[inbox_key] = f"Email - {inbox_label}"

    for channel in (ChannelType.whatsapp, ChannelType.facebook_messenger, ChannelType.instagram_dm):
        channel_key = str(channel)
        if channel_key not in channel_breakdown:
            channel_breakdown[channel_key] = 0

    return templates.TemplateResponse(
        "agent/my_performance.html",
        {
            "request": request,
            "user": current_user,
            "current_user": current_user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "my-performance",
            "active_menu": "reports",
            "days": days,
            "custom_range": date_range["custom_range"],
            "start_date": date_range["start_date"],
            "end_date": date_range["end_date"],
            "max_date": now.date().isoformat(),
            "date_range_error": date_range["error"],
            "report_ready": report_ready,
            "selected_channel_type": channel_value,
            "channel_types": [t.value for t in ChannelType],
            "total_conversations": total_conversations,
            "resolved_conversations": resolved_conversations,
            "resolution_rate": resolution_rate,
            "avg_frt_minutes": avg_frt,
            "avg_resolution_minutes": avg_resolution_time,
            "total_messages": inbox_stats.get("messages", {}).get("total", 0),
            "inbound_messages": inbox_stats.get("messages", {}).get("inbound", 0),
            "outbound_messages": inbox_stats.get("messages", {}).get("outbound", 0),
            "agent_stats": agent_stats,
            "current_agent_stats": current_agent_stats,
            "current_agent_id": metrics["agent_id"],
            "presence_hours": metrics.get("presence_hours", {}),
            "trend_data": metrics["trend_data"],
            "channel_breakdown": channel_breakdown,
            "channel_labels": channel_labels,
            "sales_stats": metrics["sales_stats"],
            "leaderboard_data": leaderboard,
        },
    )
