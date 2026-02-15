import enum
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class PerformanceDomain(enum.Enum):
    support = "support"
    operations = "operations"
    field_service = "field_service"
    communication = "communication"
    sales = "sales"
    data_quality = "data_quality"


class GoalStatus(enum.Enum):
    active = "active"
    achieved = "achieved"
    missed = "missed"
    canceled = "canceled"


class AgentPerformanceScore(Base):
    __tablename__ = "agent_performance_scores"
    __table_args__ = (
        UniqueConstraint("person_id", "score_period_start", "domain", name="uq_perf_score_person_period_domain"),
        Index("ix_perf_score_person_period", "person_id", "score_period_start"),
        Index("ix_perf_score_domain", "domain"),
        Index("ix_perf_score_period", "score_period_start"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=False)
    score_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    score_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    domain: Mapped[PerformanceDomain] = mapped_column(Enum(PerformanceDomain), nullable=False)
    raw_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    weighted_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    metrics_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    person = relationship("Person")


class AgentPerformanceSnapshot(Base):
    __tablename__ = "agent_performance_snapshots"
    __table_args__ = (
        UniqueConstraint("person_id", "score_period_start", "score_period_end", name="uq_perf_snapshot_person_period"),
        Index("ix_perf_snapshot_period", "score_period_start"),
        Index("ix_perf_snapshot_composite", "composite_score"),
        Index("ix_perf_snapshot_team_period", "team_id", "score_period_start"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=False)
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("service_teams.id"))
    score_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    score_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    composite_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    domain_scores_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    weights_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    team_type: Mapped[str | None] = mapped_column(String(40))
    sales_activity_ratio: Mapped[float | None] = mapped_column(Numeric(8, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    person = relationship("Person")
    team = relationship("ServiceTeam")


class AgentPerformanceReview(Base):
    __tablename__ = "agent_performance_reviews"
    __table_args__ = (
        UniqueConstraint("person_id", "review_period_start", "review_period_end", name="uq_perf_review_person_period"),
        Index("ix_perf_review_person_period", "person_id", "review_period_start"),
        Index("ix_perf_review_ack", "is_acknowledged"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=False)
    review_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    review_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    composite_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    domain_scores_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False)
    strengths_json: Mapped[list] = mapped_column(JSON, nullable=False)
    improvements_json: Mapped[list] = mapped_column(JSON, nullable=False)
    recommendations_json: Mapped[list] = mapped_column(JSON, nullable=False)
    callouts_json: Mapped[list] = mapped_column(JSON, nullable=False)
    llm_model: Mapped[str] = mapped_column(String(100), nullable=False)
    llm_provider: Mapped[str] = mapped_column(String(40), nullable=False, default="vllm")
    llm_tokens_in: Mapped[int | None] = mapped_column()
    llm_tokens_out: Mapped[int | None] = mapped_column()
    is_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    person = relationship("Person")


class AgentPerformanceGoal(Base):
    __tablename__ = "agent_performance_goals"
    __table_args__ = (
        Index("ix_perf_goal_person_status", "person_id", "status"),
        Index("ix_perf_goal_deadline", "deadline"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"), nullable=False)
    domain: Mapped[PerformanceDomain] = mapped_column(Enum(PerformanceDomain), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    target_value: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    current_value: Mapped[float | None] = mapped_column(Numeric(12, 2))
    comparison: Mapped[str] = mapped_column(String(10), nullable=False)
    deadline: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[GoalStatus] = mapped_column(Enum(GoalStatus), default=GoalStatus.active)
    created_by_person_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("people.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    person = relationship("Person", foreign_keys=[person_id])
    created_by = relationship("Person", foreign_keys=[created_by_person_id])
