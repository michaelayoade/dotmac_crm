import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.crm.enums import ChannelType


class AiIntakeConfig(Base):
    __tablename__ = "crm_ai_intake_configs"
    __table_args__ = (UniqueConstraint("scope_key", name="uq_crm_ai_intake_configs_scope_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope_key: Mapped[str] = mapped_column(String(160), nullable=False)
    channel_type: Mapped[ChannelType] = mapped_column(Enum(ChannelType), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confidence_threshold: Mapped[float] = mapped_column(Float, default=0.75, nullable=False)
    allow_followup_questions: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    max_clarification_turns: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    escalate_after_minutes: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    exclude_campaign_attribution: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fallback_team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    instructions: Mapped[str | None] = mapped_column(Text)
    department_mappings: Mapped[list | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
