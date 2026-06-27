from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.crm.reports import (
    FieldServiceMetricsResponse,
    InboxKpisResponse,
    PipelineStageMetricsResponse,
    ProjectMetricsResponse,
    SupportMetricsResponse,
)
from app.services.crm import reports as crm_reports

router = APIRouter(prefix="/crm/reports", tags=["crm-reports"])


@router.get("/support", response_model=SupportMetricsResponse)
def support_metrics(
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    agent_id: str | None = None,
    team_id: str | None = None,
    db: Session = Depends(get_db),
):
    return crm_reports.ticket_support_metrics(db, start_at, end_at, agent_id, team_id)


@router.get("/inbox", response_model=InboxKpisResponse)
def inbox_metrics(
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    channel_type: str | None = None,
    agent_id: str | None = None,
    team_id: str | None = None,
    db: Session = Depends(get_db),
):
    return crm_reports.inbox_kpis(db, start_at, end_at, channel_type, agent_id, team_id)


@router.get("/pipeline", response_model=PipelineStageMetricsResponse)
def pipeline_metrics(pipeline_id: str, db: Session = Depends(get_db)):
    return crm_reports.pipeline_stage_metrics(db, pipeline_id)


@router.get("/field-services", response_model=FieldServiceMetricsResponse)
def field_service_metrics(
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    agent_id: str | None = None,
    team_id: str | None = None,
    db: Session = Depends(get_db),
):
    return crm_reports.field_service_metrics(db, start_at, end_at, agent_id, team_id)


@router.get("/projects", response_model=ProjectMetricsResponse)
def project_metrics(
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    agent_id: str | None = None,
    team_id: str | None = None,
    db: Session = Depends(get_db),
):
    return crm_reports.project_metrics(db, start_at, end_at, agent_id, team_id)


def _default_range(start_at: datetime | None, end_at: datetime | None) -> tuple[datetime, datetime]:
    from datetime import UTC, timedelta

    end = end_at or datetime.now(UTC)
    start = start_at or (end - timedelta(days=7))
    return start, end


@router.get("/queue-wait")
def queue_wait_metrics(
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    team_id: str | None = None,
    db: Session = Depends(get_db),
):
    start, end = _default_range(start_at, end_at)
    return crm_reports.queue_wait_metrics(db, start, end, team_id=team_id)


@router.get("/classification")
def classification_metrics(
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
):
    start, end = _default_range(start_at, end_at)
    return crm_reports.issue_classification_breakdown(db, start, end)


@router.get("/agent-performance")
def agent_performance(
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    agent_id: str | None = None,
    team_id: str | None = None,
    channel_type: str | None = None,
    db: Session = Depends(get_db),
):
    return crm_reports.agent_performance_metrics(db, start_at, end_at, agent_id, team_id, channel_type)


@router.get("/agent-weekly")
def agent_weekly(
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    db: Session = Depends(get_db),
):
    start, end = _default_range(start_at, end_at)
    return crm_reports.agent_weekly_performance(db, start, end)


@router.get("/conversation-trend")
def conversation_trend(
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    agent_id: str | None = None,
    team_id: str | None = None,
    channel_type: str | None = None,
    db: Session = Depends(get_db),
):
    start, end = _default_range(start_at, end_at)
    return crm_reports.conversation_trend(db, start, end, agent_id, team_id, channel_type)


@router.get("/sales-pipeline")
def sales_pipeline(
    pipeline_id: str | None = None,
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    owner_agent_id: str | None = None,
    db: Session = Depends(get_db),
):
    return crm_reports.sales_pipeline_metrics(db, pipeline_id, start_at, end_at, owner_agent_id)


@router.get("/sales-forecast")
def sales_forecast(
    pipeline_id: str | None = None,
    months_ahead: int = Query(default=6, ge=1, le=24),
    db: Session = Depends(get_db),
):
    return crm_reports.sales_forecast(db, pipeline_id, months_ahead)


@router.get("/agent-sales")
def agent_sales(
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    pipeline_id: str | None = None,
    db: Session = Depends(get_db),
):
    return crm_reports.agent_sales_performance(db, start_at, end_at, pipeline_id)
