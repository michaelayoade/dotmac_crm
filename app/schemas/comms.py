from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.comms import (
    CustomerNotificationStatus,
    CustomerSurveyStatus,
    SurveyInvitationStatus,
    SurveyQuestionType,
    SurveyTriggerType,
)


class CustomerNotificationBase(BaseModel):
    entity_type: str = Field(min_length=1, max_length=40)
    entity_id: UUID
    channel: str = Field(min_length=1, max_length=40)
    recipient: str = Field(min_length=1, max_length=255)
    message: str = Field(min_length=1)
    status: CustomerNotificationStatus = CustomerNotificationStatus.pending
    sent_at: datetime | None = None


class CustomerNotificationCreate(CustomerNotificationBase):
    pass


class CustomerNotificationUpdate(BaseModel):
    status: CustomerNotificationStatus | None = None
    sent_at: datetime | None = None


class CustomerNotificationRead(CustomerNotificationBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime


class EtaUpdateBase(BaseModel):
    work_order_id: UUID
    eta_at: datetime
    note: str | None = None


class EtaUpdateCreate(EtaUpdateBase):
    pass


class EtaUpdateRead(EtaUpdateBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime


# ── Survey Question Schema ────────────────────────────────────────


class SurveyQuestion(BaseModel):
    """Validates an individual survey question definition."""

    key: str = Field(min_length=1, max_length=80, description="Unique key for this question (e.g. 'q1')")
    type: SurveyQuestionType
    label: str = Field(min_length=1, max_length=500, description="Question text shown to respondent")
    required: bool = True
    options: list[str] | None = Field(default=None, description="Choices for multiple_choice type")


# ── Survey Schemas ────────────────────────────────────────────────


class SurveyBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    questions: list[dict] | None = None
    is_active: bool = True


class SurveyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    questions: list[SurveyQuestion] | None = None
    trigger_type: SurveyTriggerType = SurveyTriggerType.manual
    public_slug: str | None = Field(default=None, min_length=1, max_length=120)
    thank_you_message: str | None = None
    expires_at: datetime | None = None
    segment_filter: dict | None = None


class SurveyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    questions: list[SurveyQuestion] | None = None
    trigger_type: SurveyTriggerType | None = None
    public_slug: str | None = Field(default=None, min_length=1, max_length=120)
    thank_you_message: str | None = None
    expires_at: datetime | None = None
    segment_filter: dict | None = None
    is_active: bool | None = None


class SurveyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None = None
    questions: list[dict] | None = None
    is_active: bool
    status: CustomerSurveyStatus
    trigger_type: SurveyTriggerType
    public_slug: str | None = None
    thank_you_message: str | None = None
    expires_at: datetime | None = None
    segment_filter: dict | None = None
    created_by_id: UUID | None = None
    total_invited: int = 0
    total_responses: int = 0
    avg_rating: float | None = None
    nps_score: float | None = None
    created_at: datetime
    updated_at: datetime


# ── Survey Response Schemas ───────────────────────────────────────


class SurveyResponseBase(BaseModel):
    survey_id: UUID
    work_order_id: UUID | None = None
    ticket_id: UUID | None = None
    responses: dict | None = None
    rating: int | None = Field(default=None, ge=1, le=5)


class SurveyResponseCreate(SurveyResponseBase):
    pass


class SurveyResponseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    survey_id: UUID
    work_order_id: UUID | None = None
    ticket_id: UUID | None = None
    invitation_id: UUID | None = None
    person_id: UUID | None = None
    responses: dict | None = None
    rating: int | None = None
    completed_at: datetime | None = None
    created_at: datetime


class SurveyPublicSubmit(BaseModel):
    """Payload from public survey form submission."""

    answers: dict = Field(description="Mapping of question key to answer value")


# ── Survey Invitation Schemas ─────────────────────────────────────


class SurveyInvitationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    survey_id: UUID
    person_id: UUID
    token: str
    email: str
    status: SurveyInvitationStatus
    ticket_id: UUID | None = None
    work_order_id: UUID | None = None
    sent_at: datetime | None = None
    opened_at: datetime | None = None
    completed_at: datetime | None = None
    expires_at: datetime | None = None
    created_at: datetime


# ── Survey Analytics Schema ───────────────────────────────────────


class QuestionBreakdown(BaseModel):
    key: str
    label: str
    type: str
    response_count: int = 0
    avg_value: float | None = None
    distribution: dict | None = None


class SurveyAnalytics(BaseModel):
    total_invited: int = 0
    total_responses: int = 0
    response_rate: float = 0.0
    avg_rating: float | None = None
    nps_score: float | None = None
    question_breakdown: list[QuestionBreakdown] = []
