"""Automation rules service.

Provides CRUD for automation rules and execution log queries.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import String, cast, func, or_
from sqlalchemy.orm import Session

from app.models.automation_rule import (
    AutomationLogOutcome,
    AutomationRule,
    AutomationRuleLog,
    AutomationRuleStatus,
)
from app.services.common import apply_ordering, apply_pagination, coerce_uuid, validate_enum
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


class AutomationRulesManager(ListResponseMixin):
    @staticmethod
    def list(
        db: Session,
        *,
        status: str | None = None,
        event_type: str | None = None,
        search: str | None = None,
        is_active: bool | None = None,
        order_by: str = "priority",
        order_dir: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> list[AutomationRule]:
        query = db.query(AutomationRule)

        status_value = None
        if status:
            status_value = validate_enum(status, AutomationRuleStatus, "status")
            query = query.filter(AutomationRule.status == status_value)
        if event_type:
            query = query.filter(AutomationRule.event_type == event_type)
        if search:
            like = f"%{search.strip()}%"
            query = query.filter(
                or_(
                    AutomationRule.name.ilike(like),
                    AutomationRule.description.ilike(like),
                    cast(AutomationRule.id, String).ilike(like),
                )
            )
        if is_active is None:
            if status_value != AutomationRuleStatus.archived:
                query = query.filter(AutomationRule.is_active.is_(True))
        else:
            query = query.filter(AutomationRule.is_active == is_active)

        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {
                "priority": AutomationRule.priority,
                "created_at": AutomationRule.created_at,
                "updated_at": AutomationRule.updated_at,
                "name": AutomationRule.name,
                "execution_count": AutomationRule.execution_count,
            },
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def get(db: Session, rule_id: str) -> AutomationRule:
        rule = db.get(AutomationRule, coerce_uuid(rule_id))
        if not rule:
            raise HTTPException(status_code=404, detail="Automation rule not found")
        return rule

    @staticmethod
    def create(
        db: Session,
        payload: AutomationRuleCreate,  # noqa: F821
        created_by_id: str | None = None,
    ) -> AutomationRule:
        data = payload.model_dump()
        if created_by_id:
            data["created_by_id"] = coerce_uuid(created_by_id)
        rule = AutomationRule(**data)
        db.add(rule)
        db.commit()
        db.refresh(rule)
        return rule

    @staticmethod
    def update(
        db: Session,
        rule_id: str,
        payload: AutomationRuleUpdate,  # noqa: F821
    ) -> AutomationRule:
        rule = db.get(AutomationRule, coerce_uuid(rule_id))
        if not rule:
            raise HTTPException(status_code=404, detail="Automation rule not found")
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            setattr(rule, key, value)
        db.commit()
        db.refresh(rule)
        return rule

    @staticmethod
    def delete(db: Session, rule_id: str) -> None:
        """Soft delete an automation rule."""
        rule = db.get(AutomationRule, coerce_uuid(rule_id))
        if not rule:
            raise HTTPException(status_code=404, detail="Automation rule not found")
        rule.is_active = False
        rule.status = AutomationRuleStatus.archived
        db.commit()

    @staticmethod
    def toggle_status(db: Session, rule_id: str, status: AutomationRuleStatus) -> AutomationRule:
        rule = db.get(AutomationRule, coerce_uuid(rule_id))
        if not rule:
            raise HTTPException(status_code=404, detail="Automation rule not found")
        rule.status = status
        if status == AutomationRuleStatus.active:
            rule.is_active = True
        db.commit()
        db.refresh(rule)
        return rule

    @staticmethod
    def get_active_rules_for_event(db: Session, event_type_value: str) -> list[AutomationRule]:
        """Hot path query using the composite index."""
        return (
            db.query(AutomationRule)
            .filter(
                AutomationRule.event_type == event_type_value,
                AutomationRule.status == AutomationRuleStatus.active,
                AutomationRule.is_active.is_(True),
            )
            .order_by(AutomationRule.priority.desc())
            .all()
        )

    @staticmethod
    def record_execution(
        db: Session,
        rule: AutomationRule,
        event_id: UUID,  # noqa: F821
        event_type: str,
        outcome: AutomationLogOutcome,
        actions_executed: list[dict],
        duration_ms: int,
        error: str | None = None,
    ) -> AutomationRuleLog:
        log = AutomationRuleLog(
            rule_id=rule.id,
            event_id=event_id,
            event_type=event_type,
            outcome=outcome,
            actions_executed=actions_executed,
            duration_ms=duration_ms,
            error=error,
        )
        db.add(log)

        rule.execution_count = (rule.execution_count or 0) + 1
        rule.last_triggered_at = datetime.now(UTC)

        db.commit()
        db.refresh(log)
        return log

    @staticmethod
    def recent_logs(db: Session, rule_id: str, limit: int = 20) -> list[AutomationRuleLog]:
        return (
            db.query(AutomationRuleLog)
            .filter(AutomationRuleLog.rule_id == coerce_uuid(rule_id))
            .order_by(AutomationRuleLog.created_at.desc())
            .limit(limit)
            .all()
        )

    @staticmethod
    def count_by_status(db: Session) -> dict:
        results = (
            db.query(AutomationRule.status, func.count(AutomationRule.id))
            .filter(AutomationRule.is_active.is_(True))
            .group_by(AutomationRule.status)
            .all()
        )
        counts = {s.value: 0 for s in AutomationRuleStatus}
        for status_val, count in results:
            if status_val:
                counts[status_val.value] = count
        counts["total"] = sum(counts.values())
        return counts


automation_rules_service = AutomationRulesManager()
