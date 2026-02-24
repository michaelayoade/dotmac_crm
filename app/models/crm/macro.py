import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.crm.enums import MacroVisibility


class CrmConversationMacro(Base):
    __tablename__ = "crm_conversation_macros"
    __table_args__ = (
        Index("ix_crm_macros_visibility_active", "visibility", "is_active"),
        Index("ix_crm_macros_agent_active", "created_by_agent_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[MacroVisibility] = mapped_column(
        Enum(MacroVisibility, name="macrovisibility", create_constraint=False),
        nullable=False,
        default=MacroVisibility.personal,
    )
    created_by_agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_agents.id"), nullable=False
    )
    actions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    execution_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    created_by_agent = relationship("CrmAgent", back_populates="macros", foreign_keys=[created_by_agent_id])
