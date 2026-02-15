from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.performance import AgentPerformanceGoal, AgentPerformanceScore, GoalStatus
from app.schemas.performance import AgentPerformanceGoalCreate, AgentPerformanceGoalUpdate
from app.services.common import coerce_uuid


class PerformanceGoalsService:
    def list(self, db: Session, person_id: str | None = None) -> list[AgentPerformanceGoal]:
        query = db.query(AgentPerformanceGoal)
        if person_id:
            query = query.filter(AgentPerformanceGoal.person_id == coerce_uuid(person_id))
        return query.order_by(AgentPerformanceGoal.created_at.desc()).all()

    def get(self, db: Session, goal_id: str) -> AgentPerformanceGoal:
        goal = db.get(AgentPerformanceGoal, coerce_uuid(goal_id))
        if not goal:
            raise HTTPException(status_code=404, detail="Goal not found")
        return goal

    def create(
        self, db: Session, payload: AgentPerformanceGoalCreate, created_by_person_id: str | None
    ) -> AgentPerformanceGoal:
        if payload.comparison not in ("gte", "lte"):
            raise HTTPException(status_code=400, detail="comparison must be 'gte' or 'lte'")
        goal = AgentPerformanceGoal(
            person_id=coerce_uuid(payload.person_id),
            domain=payload.domain,
            metric_key=payload.metric_key,
            label=payload.label,
            target_value=payload.target_value,
            comparison=payload.comparison,
            deadline=payload.deadline,
            created_by_person_id=coerce_uuid(created_by_person_id) if created_by_person_id else None,
        )
        db.add(goal)
        db.commit()
        db.refresh(goal)
        return goal

    def update(self, db: Session, goal_id: str, payload: AgentPerformanceGoalUpdate) -> AgentPerformanceGoal:
        goal = self.get(db, goal_id)
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(goal, key, value)
        goal.updated_at = datetime.now(UTC)
        db.commit()
        db.refresh(goal)
        return goal

    def _metric_value(self, latest_score: AgentPerformanceScore | None, metric_key: str) -> float | None:
        if not latest_score:
            return None
        if metric_key in {"raw_score", "domain_score", "score"}:
            return float(latest_score.raw_score)
        if isinstance(latest_score.metrics_json, dict):
            value = latest_score.metrics_json.get(metric_key)
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None
        return None

    def refresh_progress(self, db: Session, as_of: date | None = None) -> int:
        today = as_of or datetime.now(UTC).date()
        goals = (
            db.query(AgentPerformanceGoal)
            .filter(AgentPerformanceGoal.status.in_([GoalStatus.active, GoalStatus.achieved]))
            .all()
        )

        updated = 0
        for goal in goals:
            latest_score = (
                db.query(AgentPerformanceScore)
                .filter(
                    AgentPerformanceScore.person_id == goal.person_id,
                    AgentPerformanceScore.domain == goal.domain,
                )
                .order_by(AgentPerformanceScore.score_period_end.desc())
                .first()
            )
            current_value = self._metric_value(latest_score, goal.metric_key)
            goal.current_value = current_value

            achieved = False
            if current_value is not None:
                if goal.comparison == "lte":
                    achieved = float(current_value) <= float(goal.target_value)
                else:
                    achieved = float(current_value) >= float(goal.target_value)

            if achieved:
                goal.status = GoalStatus.achieved
            elif goal.status != GoalStatus.canceled and goal.deadline < today:
                goal.status = GoalStatus.missed
            else:
                goal.status = GoalStatus.active

            goal.updated_at = datetime.now(UTC)
            updated += 1

        db.commit()
        return updated


performance_goals = PerformanceGoalsService()
