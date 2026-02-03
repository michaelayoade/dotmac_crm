"""Tests for the automation rules engine."""

import uuid
from datetime import UTC, datetime

import pytest

from app.models.automation_rule import (
    AutomationLogOutcome,
    AutomationRule,
    AutomationRuleStatus,
)
from app.models.crm.conversation import ConversationAssignment, ConversationTag
from app.schemas.automation_rule import AutomationRuleCreate, AutomationRuleUpdate
from app.services.automation_conditions import _MISSING, _resolve_field, evaluate_conditions
from app.services.automation_rules import automation_rules_service

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture()
def automation_rule(db_session):
    rule = AutomationRule(
        name="Test Rule",
        event_type="ticket.created",
        conditions=[{"field": "payload.priority", "op": "eq", "value": "urgent"}],
        actions=[{"action_type": "add_tag", "params": {"entity": "ticket", "tag": "auto-escalated"}}],
        status=AutomationRuleStatus.active,
        priority=10,
    )
    db_session.add(rule)
    db_session.commit()
    db_session.refresh(rule)
    return rule


@pytest.fixture()
def automation_rule_no_conditions(db_session):
    rule = AutomationRule(
        name="Unconditional Rule",
        event_type="ticket.created",
        conditions=[],
        actions=[
            {"action_type": "set_field", "params": {"entity": "ticket", "field": "status", "value": "in_progress"}}
        ],
        status=AutomationRuleStatus.active,
        priority=5,
    )
    db_session.add(rule)
    db_session.commit()
    db_session.refresh(rule)
    return rule


# ============================================================================
# Condition Evaluator Tests (pure unit, no DB)
# ============================================================================


class TestConditionEvaluator:
    def test_empty_conditions_returns_true(self):
        assert evaluate_conditions([], {}) is True

    def test_eq_match(self):
        conditions = [{"field": "payload.priority", "op": "eq", "value": "urgent"}]
        context = {"payload": {"priority": "urgent"}}
        assert evaluate_conditions(conditions, context) is True

    def test_eq_no_match(self):
        conditions = [{"field": "payload.priority", "op": "eq", "value": "urgent"}]
        context = {"payload": {"priority": "low"}}
        assert evaluate_conditions(conditions, context) is False

    def test_neq(self):
        conditions = [{"field": "payload.status", "op": "neq", "value": "closed"}]
        context = {"payload": {"status": "open"}}
        assert evaluate_conditions(conditions, context) is True

    def test_in_list(self):
        conditions = [{"field": "payload.priority", "op": "in", "value": ["urgent", "high"]}]
        context = {"payload": {"priority": "high"}}
        assert evaluate_conditions(conditions, context) is True

    def test_in_list_no_match(self):
        conditions = [{"field": "payload.priority", "op": "in", "value": ["urgent", "high"]}]
        context = {"payload": {"priority": "low"}}
        assert evaluate_conditions(conditions, context) is False

    def test_not_in(self):
        conditions = [{"field": "payload.priority", "op": "not_in", "value": ["low", "normal"]}]
        context = {"payload": {"priority": "urgent"}}
        assert evaluate_conditions(conditions, context) is True

    def test_contains_string(self):
        conditions = [{"field": "payload.title", "op": "contains", "value": "outage"}]
        context = {"payload": {"title": "Network outage in zone A"}}
        assert evaluate_conditions(conditions, context) is True

    def test_contains_list(self):
        conditions = [{"field": "payload.tags", "op": "contains", "value": "vip"}]
        context = {"payload": {"tags": ["vip", "urgent"]}}
        assert evaluate_conditions(conditions, context) is True

    def test_gt(self):
        conditions = [{"field": "payload.count", "op": "gt", "value": 5}]
        context = {"payload": {"count": 10}}
        assert evaluate_conditions(conditions, context) is True

    def test_lt(self):
        conditions = [{"field": "payload.count", "op": "lt", "value": 5}]
        context = {"payload": {"count": 3}}
        assert evaluate_conditions(conditions, context) is True

    def test_gte(self):
        conditions = [{"field": "payload.count", "op": "gte", "value": 5}]
        context = {"payload": {"count": 5}}
        assert evaluate_conditions(conditions, context) is True

    def test_lte(self):
        conditions = [{"field": "payload.count", "op": "lte", "value": 5}]
        context = {"payload": {"count": 5}}
        assert evaluate_conditions(conditions, context) is True

    def test_exists(self):
        conditions = [{"field": "payload.priority", "op": "exists", "value": None}]
        context = {"payload": {"priority": "urgent"}}
        assert evaluate_conditions(conditions, context) is True

    def test_exists_fails(self):
        conditions = [{"field": "payload.missing", "op": "exists", "value": None}]
        context = {"payload": {}}
        assert evaluate_conditions(conditions, context) is False

    def test_not_exists(self):
        conditions = [{"field": "payload.missing", "op": "not_exists", "value": None}]
        context = {"payload": {}}
        assert evaluate_conditions(conditions, context) is True

    def test_nested_path(self):
        conditions = [{"field": "payload.customer.tier", "op": "eq", "value": "gold"}]
        context = {"payload": {"customer": {"tier": "gold"}}}
        assert evaluate_conditions(conditions, context) is True

    def test_unknown_operator(self):
        conditions = [{"field": "payload.x", "op": "weird_op", "value": 1}]
        context = {"payload": {"x": 1}}
        assert evaluate_conditions(conditions, context) is False

    def test_multiple_conditions_and_logic(self):
        conditions = [
            {"field": "payload.priority", "op": "eq", "value": "urgent"},
            {"field": "payload.status", "op": "neq", "value": "closed"},
        ]
        context = {"payload": {"priority": "urgent", "status": "open"}}
        assert evaluate_conditions(conditions, context) is True

    def test_multiple_conditions_one_fails(self):
        conditions = [
            {"field": "payload.priority", "op": "eq", "value": "urgent"},
            {"field": "payload.status", "op": "eq", "value": "closed"},
        ]
        context = {"payload": {"priority": "urgent", "status": "open"}}
        assert evaluate_conditions(conditions, context) is False

    def test_resolve_field_missing(self):
        assert _resolve_field({}, "a.b.c") is _MISSING

    def test_numeric_comparison_with_strings(self):
        conditions = [{"field": "payload.count", "op": "gt", "value": "abc"}]
        context = {"payload": {"count": 10}}
        assert evaluate_conditions(conditions, context) is False

    def test_eq_numeric_string_coercion(self):
        """String '5' from form should match integer 5 from payload."""
        conditions = [{"field": "payload.count", "op": "eq", "value": "5"}]
        context = {"payload": {"count": 5}}
        assert evaluate_conditions(conditions, context) is True

    def test_neq_numeric_string_coercion(self):
        conditions = [{"field": "payload.count", "op": "neq", "value": "5"}]
        context = {"payload": {"count": 5}}
        assert evaluate_conditions(conditions, context) is False

    def test_eq_string_stays_string(self):
        """Non-numeric strings should not coerce."""
        conditions = [{"field": "payload.status", "op": "eq", "value": "open"}]
        context = {"payload": {"status": "closed"}}
        assert evaluate_conditions(conditions, context) is False


# ============================================================================
# Action Executor Tests
# ============================================================================


class TestActionExecutor:
    def test_set_field_on_ticket(self, db_session, ticket):
        from app.services.automation_actions import execute_actions
        from app.services.events.types import Event, EventType

        event = Event(
            event_type=EventType.ticket_created,
            payload={},
            ticket_id=ticket.id,
        )
        actions = [{"action_type": "set_field", "params": {"entity": "ticket", "field": "priority", "value": "urgent"}}]

        results = execute_actions(db_session, actions, event)
        assert len(results) == 1
        assert results[0]["success"] is True
        db_session.refresh(ticket)
        assert ticket.priority == "urgent"

    def test_set_field_whitelist_rejection(self, db_session, ticket):
        from app.services.automation_actions import execute_actions
        from app.services.events.types import Event, EventType

        event = Event(
            event_type=EventType.ticket_created,
            payload={},
            ticket_id=ticket.id,
        )
        actions = [{"action_type": "set_field", "params": {"entity": "ticket", "field": "id", "value": "bad"}}]

        results = execute_actions(db_session, actions, event)
        assert results[0]["success"] is False
        assert "not allowed" in results[0]["error"]

    def test_send_notification(self, db_session):
        from app.services.automation_actions import execute_actions
        from app.services.events.types import Event, EventType

        event = Event(
            event_type=EventType.ticket_created,
            payload={},
        )
        actions = [
            {
                "action_type": "send_notification",
                "params": {"recipient": "test@example.com", "subject": "Test", "body": "Hello"},
            }
        ]

        results = execute_actions(db_session, actions, event)
        assert results[0]["success"] is True

    def test_partial_failure(self, db_session, ticket):
        from app.services.automation_actions import execute_actions
        from app.services.events.types import Event, EventType

        event = Event(
            event_type=EventType.ticket_created,
            payload={},
            ticket_id=ticket.id,
        )
        actions = [
            {"action_type": "set_field", "params": {"entity": "ticket", "field": "id", "value": "bad"}},
            {"action_type": "set_field", "params": {"entity": "ticket", "field": "priority", "value": "high"}},
        ]

        results = execute_actions(db_session, actions, event)
        assert results[0]["success"] is False
        assert results[1]["success"] is True

    def test_unknown_action_type(self, db_session):
        from app.services.automation_actions import execute_actions
        from app.services.events.types import Event, EventType

        event = Event(event_type=EventType.ticket_created, payload={})
        results = execute_actions(db_session, [{"action_type": "nonexistent", "params": {}}], event)
        assert results[0]["success"] is False

    def test_assign_conversation(self, db_session, crm_contact, crm_agent):
        from app.services.automation_actions import execute_actions
        from app.services.events.types import Event, EventType
        from app.services.crm import conversation as conversation_service
        from app.schemas.crm.conversation import ConversationCreate

        conversation = conversation_service.Conversations.create(
            db_session,
            ConversationCreate(person_id=crm_contact.id, subject="Automation test"),
        )
        event = Event(
            event_type=EventType.custom,
            payload={"conversation_id": str(conversation.id)},
        )
        actions = [{"action_type": "assign_conversation", "params": {"agent_id": str(crm_agent.id)}}]

        results = execute_actions(db_session, actions, event)
        assert results[0]["success"] is True
        assignment = (
            db_session.query(ConversationAssignment)
            .filter(ConversationAssignment.conversation_id == conversation.id)
            .filter(ConversationAssignment.agent_id == crm_agent.id)
            .filter(ConversationAssignment.is_active.is_(True))
            .first()
        )
        assert assignment is not None

    def test_add_tag_conversation(self, db_session, crm_contact):
        from app.services.automation_actions import execute_actions
        from app.services.events.types import Event, EventType
        from app.services.crm import conversation as conversation_service
        from app.schemas.crm.conversation import ConversationCreate

        conversation = conversation_service.Conversations.create(
            db_session,
            ConversationCreate(person_id=crm_contact.id, subject="Tag test"),
        )
        event = Event(
            event_type=EventType.custom,
            payload={"conversation_id": str(conversation.id)},
        )
        actions = [{"action_type": "add_tag", "params": {"entity": "conversation", "tag": "auto"}}]

        results = execute_actions(db_session, actions, event)
        assert results[0]["success"] is True
        tag = (
            db_session.query(ConversationTag)
            .filter(ConversationTag.conversation_id == conversation.id)
            .filter(ConversationTag.tag == "auto")
            .first()
        )
        assert tag is not None


# ============================================================================
# AutomationHandler Integration Tests
# ============================================================================


class TestAutomationHandler:
    def test_skip_high_depth(self, db_session, automation_rule):
        from app.services.events.handlers.automation import AutomationHandler
        from app.services.events.types import Event, EventType

        handler = AutomationHandler()
        event = Event(
            event_type=EventType.ticket_created,
            payload={"_automation_depth": 3, "priority": "urgent"},
        )
        # Should not raise and should not execute
        handler.handle(db_session, event)
        db_session.refresh(automation_rule)
        assert automation_rule.execution_count == 0

    def test_no_rules_noop(self, db_session):
        from app.services.events.handlers.automation import AutomationHandler
        from app.services.events.types import Event, EventType

        handler = AutomationHandler()
        event = Event(
            event_type=EventType.project_created,
            payload={},
        )
        handler.handle(db_session, event)  # Should not raise

    def test_cooldown_skip(self, db_session, automation_rule):
        from app.services.events.handlers.automation import AutomationHandler
        from app.services.events.types import Event, EventType

        automation_rule.cooldown_seconds = 3600
        automation_rule.last_triggered_at = datetime.now(UTC)
        db_session.commit()

        handler = AutomationHandler()
        event = Event(
            event_type=EventType.ticket_created,
            payload={"priority": "urgent"},
        )
        handler.handle(db_session, event)
        db_session.refresh(automation_rule)
        assert automation_rule.execution_count == 0

    def test_condition_mismatch(self, db_session, automation_rule):
        from app.services.events.handlers.automation import AutomationHandler
        from app.services.events.types import Event, EventType

        handler = AutomationHandler()
        event = Event(
            event_type=EventType.ticket_created,
            payload={"priority": "low"},
        )
        handler.handle(db_session, event)
        db_session.refresh(automation_rule)
        assert automation_rule.execution_count == 0

    def test_stop_after_match(self, db_session):
        from app.services.events.handlers.automation import AutomationHandler
        from app.services.events.types import Event, EventType

        rule1 = AutomationRule(
            name="High priority rule",
            event_type="ticket.created",
            conditions=[],
            actions=[{"action_type": "send_notification", "params": {"recipient": "a@b.com", "subject": "R1"}}],
            status=AutomationRuleStatus.active,
            priority=20,
            stop_after_match=True,
        )
        rule2 = AutomationRule(
            name="Low priority rule",
            event_type="ticket.created",
            conditions=[],
            actions=[{"action_type": "send_notification", "params": {"recipient": "a@b.com", "subject": "R2"}}],
            status=AutomationRuleStatus.active,
            priority=5,
        )
        db_session.add_all([rule1, rule2])
        db_session.commit()

        handler = AutomationHandler()
        event = Event(
            event_type=EventType.ticket_created,
            payload={},
        )
        handler.handle(db_session, event)

        db_session.refresh(rule1)
        db_session.refresh(rule2)
        assert rule1.execution_count == 1
        assert rule2.execution_count == 0


# ============================================================================
# Service CRUD Tests
# ============================================================================


class TestAutomationRulesService:
    def test_create(self, db_session):
        payload = AutomationRuleCreate(
            name="New Rule",
            event_type="ticket.created",
            conditions=[],
            actions=[{"action_type": "add_tag", "params": {"entity": "ticket", "tag": "new"}}],
        )
        rule = automation_rules_service.create(db_session, payload)
        assert rule.id is not None
        assert rule.name == "New Rule"
        assert rule.status == AutomationRuleStatus.active

    def test_get(self, db_session, automation_rule):
        fetched = automation_rules_service.get(db_session, str(automation_rule.id))
        assert fetched.id == automation_rule.id

    def test_get_not_found(self, db_session):
        from fastapi import HTTPException

        with pytest.raises(HTTPException):
            automation_rules_service.get(db_session, str(uuid.uuid4()))

    def test_list_filters(self, db_session, automation_rule):
        items = automation_rules_service.list(db_session, event_type="ticket.created")
        assert any(r.id == automation_rule.id for r in items)

        items = automation_rules_service.list(db_session, event_type="nonexistent")
        assert not any(r.id == automation_rule.id for r in items)

    def test_list_search(self, db_session, automation_rule):
        items = automation_rules_service.list(db_session, search="Test Rule")
        assert any(r.id == automation_rule.id for r in items)

    def test_update(self, db_session, automation_rule):
        payload = AutomationRuleUpdate(name="Updated Rule")
        updated = automation_rules_service.update(db_session, str(automation_rule.id), payload)
        assert updated.name == "Updated Rule"

    def test_delete_soft(self, db_session, automation_rule):
        automation_rules_service.delete(db_session, str(automation_rule.id))
        db_session.refresh(automation_rule)
        assert automation_rule.is_active is False
        assert automation_rule.status == AutomationRuleStatus.archived

    def test_toggle_status(self, db_session, automation_rule):
        rule = automation_rules_service.toggle_status(db_session, str(automation_rule.id), AutomationRuleStatus.paused)
        assert rule.status == AutomationRuleStatus.paused

    def test_get_active_rules_for_event(self, db_session, automation_rule):
        rules = automation_rules_service.get_active_rules_for_event(db_session, "ticket.created")
        assert any(r.id == automation_rule.id for r in rules)

    def test_get_active_rules_excludes_inactive(self, db_session, automation_rule):
        automation_rule.is_active = False
        db_session.commit()
        rules = automation_rules_service.get_active_rules_for_event(db_session, "ticket.created")
        assert not any(r.id == automation_rule.id for r in rules)

    def test_count_by_status(self, db_session, automation_rule):
        counts = automation_rules_service.count_by_status(db_session)
        assert counts["total"] >= 1
        assert counts["active"] >= 1

    def test_record_execution(self, db_session, automation_rule):
        log = automation_rules_service.record_execution(
            db_session,
            automation_rule,
            uuid.uuid4(),
            "ticket.created",
            AutomationLogOutcome.success,
            [{"action_type": "add_tag", "success": True}],
            42,
        )
        assert log.id is not None
        assert log.outcome == AutomationLogOutcome.success
        db_session.refresh(automation_rule)
        assert automation_rule.execution_count == 1

    def test_recent_logs(self, db_session, automation_rule):
        automation_rules_service.record_execution(
            db_session,
            automation_rule,
            uuid.uuid4(),
            "ticket.created",
            AutomationLogOutcome.success,
            [],
            10,
        )
        logs = automation_rules_service.recent_logs(db_session, str(automation_rule.id))
        assert len(logs) >= 1
