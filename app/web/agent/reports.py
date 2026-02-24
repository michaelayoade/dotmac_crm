"""Agent performance report routes."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.crm.enums import ChannelType
from app.services.crm import reports as crm_reports_service
from app.services.crm.inbox.agents import get_current_agent_id
from app.services.crm.inbox.permissions import can_view_inbox
from app.web.admin import get_current_user, get_sidebar_stats
from app.web.auth.dependencies import require_web_auth

router = APIRouter(
    prefix="/agent",
    tags=["web-agent"],
    dependencies=[Depends(require_web_auth)],
)
templates = Jinja2Templates(directory="templates")


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
            channel_type=None,
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
            channel_type=None,
        )
        trend_data = crm_reports_service.conversation_trend(
            db=db,
            start_at=start_at,
            end_at=end_at,
            agent_id=agent_id,
            team_id=None,
            channel_type=None,
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
):
    current_user = get_current_user(request)
    roles = current_user.get("roles") or []
    scopes = current_user.get("permissions") or []
    if not can_view_inbox(roles, scopes):
        raise HTTPException(status_code=403, detail="Forbidden")

    now = datetime.now(UTC)
    start_date = now - timedelta(days=days)

    metrics = _load_my_performance_metrics(
        db=db,
        person_id=current_user.get("person_id"),
        start_at=start_date,
        end_at=now,
    )
    leaderboard = _load_leaderboard_metrics(
        db=db,
        start_at=start_date,
        end_at=now,
    )

    agent_stats = metrics["agent_stats"]
    inbox_stats = metrics["inbox_stats"]
    current_agent_stats = agent_stats[0] if agent_stats else None

    total_conversations = sum(agent["total_conversations"] for agent in agent_stats)
    resolved_conversations = sum(agent["resolved_conversations"] for agent in agent_stats)
    resolution_rate = resolved_conversations / total_conversations * 100 if total_conversations > 0 else 0

    total_team_response_minutes = sum(
        (a["avg_first_response_minutes"] or 0) * a["total_conversations"]
        for a in agent_stats
        if a["avg_first_response_minutes"] is not None
    )
    total_convos_with_frt = sum(
        a["total_conversations"] for a in agent_stats if a["avg_first_response_minutes"] is not None
    )
    avg_frt = total_team_response_minutes / total_convos_with_frt if total_convos_with_frt > 0 else None

    total_resolution_minutes = sum(
        (a["avg_resolution_minutes"] or 0) * a["resolved_conversations"]
        for a in agent_stats
        if a["avg_resolution_minutes"] is not None
    )
    avg_resolution_time = total_resolution_minutes / resolved_conversations if resolved_conversations > 0 else None

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
            "trend_data": metrics["trend_data"],
            "channel_breakdown": channel_breakdown,
            "channel_labels": channel_labels,
            "sales_stats": metrics["sales_stats"],
            "leaderboard_data": leaderboard,
        },
    )
