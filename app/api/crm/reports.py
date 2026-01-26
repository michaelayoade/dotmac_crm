from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.schemas.crm.reports import (
    InboxKpisResponse,
    PipelineStageMetricsResponse,
    SupportMetricsResponse,
    FieldServiceMetricsResponse,
    ProjectMetricsResponse,
)
from app.services.crm import reports as crm_reports

router = APIRouter(prefix="/crm/reports", tags=["crm-reports"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
