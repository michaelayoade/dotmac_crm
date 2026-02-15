from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.ai_insight import AIInsightStatus, InsightDomain, InsightSeverity


class AIInsightRead(BaseModel):
    id: str | UUID
    persona_key: str
    domain: InsightDomain
    severity: InsightSeverity
    status: AIInsightStatus
    entity_type: str
    entity_id: str | None
    title: str
    summary: str
    structured_output: dict | None = None
    confidence_score: float | None = None
    recommendations: list | None = None
    llm_provider: str
    llm_model: str
    llm_tokens_in: int | None = None
    llm_tokens_out: int | None = None
    llm_endpoint: str | None = None
    generation_time_ms: int | None = None
    trigger: str
    triggered_by_person_id: str | UUID | None = None
    acknowledged_at: datetime | None = None
    acknowledged_by_person_id: str | UUID | None = None
    expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AnalyzeRequest(BaseModel):
    entity_type: str
    entity_id: str | None = None
    params: dict = {}
