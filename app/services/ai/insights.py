from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ai_insight import AIInsight, AIInsightStatus, InsightDomain, InsightSeverity
from app.services.common import apply_ordering, apply_pagination, coerce_uuid
from app.services.response import ListResponseMixin


class AIInsights(ListResponseMixin):
    def list(
        self,
        db: Session,
        *,
        domain: str | None,
        persona_key: str | None,
        entity_type: str | None,
        entity_id: str | None,
        status: str | None,
        severity: str | None,
        limit: int,
        offset: int,
    ) -> list[AIInsight]:
        query = db.query(AIInsight)
        if domain:
            query = query.filter(AIInsight.domain == InsightDomain(domain))
        if persona_key:
            query = query.filter(AIInsight.persona_key == persona_key)
        if entity_type:
            query = query.filter(AIInsight.entity_type == entity_type)
        if entity_id:
            query = query.filter(AIInsight.entity_id == entity_id)
        if status:
            query = query.filter(AIInsight.status == AIInsightStatus(status))
        if severity:
            query = query.filter(AIInsight.severity == InsightSeverity(severity))

        query = apply_ordering(
            query,
            "created_at",
            "desc",
            {"created_at": AIInsight.created_at},
        )
        return apply_pagination(query, limit, offset).all()

    def get(self, db: Session, insight_id: str) -> AIInsight:
        insight = db.get(AIInsight, coerce_uuid(insight_id))
        if not insight:
            raise HTTPException(status_code=404, detail="Insight not found")
        return insight

    def acknowledge(self, db: Session, insight_id: str, person_id: str) -> AIInsight:
        insight = self.get(db, insight_id)
        insight.status = AIInsightStatus.acknowledged
        insight.acknowledged_at = datetime.now(UTC)
        insight.acknowledged_by_person_id = coerce_uuid(person_id)
        db.commit()
        db.refresh(insight)
        return insight

    def expire_stale(self, db: Session) -> int:
        now = datetime.now(UTC)
        rows = (
            db.query(AIInsight)
            .filter(AIInsight.expires_at.isnot(None), AIInsight.expires_at <= now)
            .filter(AIInsight.status.in_([AIInsightStatus.completed, AIInsightStatus.pending]))
            .all()
        )
        for row in rows:
            row.status = AIInsightStatus.expired
        db.commit()
        return len(rows)

    def tokens_used_today(self, db: Session) -> int:
        # Sum the persisted usage for completed insights for the current UTC date.
        today = datetime.now(UTC).date()
        total = (
            db.query(
                func.coalesce(
                    func.sum(func.coalesce(AIInsight.llm_tokens_in, 0) + func.coalesce(AIInsight.llm_tokens_out, 0)), 0
                )
            )
            .filter(func.date(AIInsight.created_at) == today)
            .filter(AIInsight.status == AIInsightStatus.completed)
            .scalar()
        )
        return int(total or 0)


ai_insights = AIInsights()
