from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models.performance import GoalStatus, PerformanceDomain


class AgentPerformanceScoreRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    person_id: str
    score_period_start: datetime
    score_period_end: datetime
    domain: PerformanceDomain
    raw_score: float
    weighted_score: float
    metrics_json: dict | None = None


class AgentPerformanceSnapshotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    person_id: str
    score_period_start: datetime
    score_period_end: datetime
    composite_score: float
    domain_scores_json: dict
    weights_json: dict
    team_id: str | None = None


class AgentPerformanceReviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    person_id: str
    review_period_start: datetime
    review_period_end: datetime
    composite_score: float
    domain_scores_json: dict
    summary_text: str
    strengths_json: list
    improvements_json: list
    recommendations_json: list
    callouts_json: list
    llm_model: str
    llm_provider: str
    llm_tokens_in: int | None = None
    llm_tokens_out: int | None = None
    is_acknowledged: bool
    acknowledged_at: datetime | None = None
    created_at: datetime


class AgentPerformanceGoalBase(BaseModel):
    person_id: str
    domain: PerformanceDomain
    metric_key: str
    label: str
    target_value: float
    comparison: str
    deadline: date


class AgentPerformanceGoalCreate(AgentPerformanceGoalBase):
    pass


class AgentPerformanceGoalUpdate(BaseModel):
    label: str | None = None
    target_value: float | None = None
    current_value: float | None = None
    comparison: str | None = None
    deadline: date | None = None
    status: GoalStatus | None = None


class AgentPerformanceGoalRead(AgentPerformanceGoalBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    current_value: float | None = None
    status: GoalStatus
    created_by_person_id: str | None = None
    created_at: datetime
    updated_at: datetime
