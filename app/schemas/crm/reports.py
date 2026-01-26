from datetime import datetime

from pydantic import BaseModel


class SupportMetricsResponse(BaseModel):
    tickets: dict
    avg_resolution_hours: float | None = None
    sla: dict


class InboxKpisResponse(BaseModel):
    messages: dict
    avg_response_minutes: float | None = None
    avg_resolution_minutes: float | None = None


class PipelineStageMetricsResponse(BaseModel):
    total_leads: int
    won: int
    lost: int
    conversion_percent: float | None = None
    stages: dict


class FieldServiceMetricsResponse(BaseModel):
    total: int
    status: dict
    avg_completion_hours: float | None = None


class ProjectMetricsResponse(BaseModel):
    projects: dict
    tasks: dict


class ReportFilters(BaseModel):
    start_at: datetime | None = None
    end_at: datetime | None = None
