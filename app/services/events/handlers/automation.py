"""Automation handler for the event system.

Evaluates database-configured automation rules against fired events
and executes matching rule actions.
"""

import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.automation_rule import AutomationLogOutcome
from app.services.automation_actions import execute_actions
from app.services.automation_conditions import evaluate_conditions
from app.services.automation_rules import automation_rules_service
from app.services.events.types import Event

logger = logging.getLogger(__name__)

_MAX_AUTOMATION_DEPTH = 3


class AutomationHandler:
    """Handler that evaluates and executes automation rules."""

    def handle(self, db: Session, event: Event) -> None:
        """Process an event against active automation rules."""
        # Loop protection
        depth = event.payload.get("_automation_depth", 0)
        if depth >= _MAX_AUTOMATION_DEPTH:
            logger.debug(
                "Skipping automation for event %s: depth %d >= %d",
                event.event_type.value,
                depth,
                _MAX_AUTOMATION_DEPTH,
            )
            return

        # Query matching rules
        rules = automation_rules_service.get_active_rules_for_event(db, event.event_type.value)
        if not rules:
            return

        # Build context for condition evaluation
        context = self._build_context(event)

        now = datetime.now(UTC)

        for rule in rules:
            # Cooldown check
            if rule.cooldown_seconds and rule.cooldown_seconds > 0 and rule.last_triggered_at:
                cooldown_until = rule.last_triggered_at + timedelta(seconds=rule.cooldown_seconds)
                if now < cooldown_until:
                    logger.debug(
                        "Rule %s skipped: cooldown active until %s",
                        rule.name,
                        cooldown_until,
                    )
                    continue

            # Evaluate conditions
            conditions = rule.conditions or []
            if not evaluate_conditions(conditions, context):
                continue

            # Execute actions
            start = time.monotonic()
            actions = rule.actions or []
            action_results = execute_actions(db, actions, event, triggered_by_automation=True)
            duration_ms = int((time.monotonic() - start) * 1000)

            # Determine outcome
            all_success = all(r["success"] for r in action_results)
            any_success = any(r["success"] for r in action_results)

            if all_success:
                outcome = AutomationLogOutcome.success
                error = None
            elif any_success:
                outcome = AutomationLogOutcome.partial_failure
                error = "; ".join(r["error"] for r in action_results if r.get("error"))
            else:
                outcome = AutomationLogOutcome.failure
                error = "; ".join(r["error"] for r in action_results if r.get("error"))

            # Record execution
            automation_rules_service.record_execution(
                db,
                rule,
                event.event_id,
                event.event_type.value,
                outcome,
                action_results,
                duration_ms,
                error,
            )

            logger.info(
                "Automation rule '%s' executed for event %s: %s (%dms)",
                rule.name,
                event.event_type.value,
                outcome.value,
                duration_ms,
            )

            # Stop after match
            if rule.stop_after_match:
                logger.debug(
                    "Rule '%s' has stop_after_match; stopping evaluation",
                    rule.name,
                )
                break

    def _build_context(self, event: Event) -> dict:
        """Build condition evaluation context from event."""
        context: dict = {
            "payload": event.payload,
            "event_type": event.event_type.value,
        }
        if event.ticket_id:
            context["ticket_id"] = str(event.ticket_id)
        if event.project_id:
            context["project_id"] = str(event.project_id)
        if event.work_order_id:
            context["work_order_id"] = str(event.work_order_id)
        if event.subscriber_id:
            context["subscriber_id"] = str(event.subscriber_id)
        if event.actor:
            context["actor"] = event.actor
        return context
