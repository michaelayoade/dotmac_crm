"""Tests for weekly agent performance report."""

import uuid
from datetime import UTC, datetime, timedelta

from app.models.crm.conversation import Conversation, ConversationAssignment
from app.models.crm.enums import ConversationPriority, ConversationStatus
from app.models.crm.team import CrmAgent
from app.models.person import Person


def _make_person(db, name="Test User") -> Person:
    person = Person(
        first_name=name.split()[0],
        last_name=name.split()[-1],
        display_name=name,
        email=f"test-{uuid.uuid4().hex[:8]}@example.com",
        is_active=True,
    )
    db.add(person)
    db.flush()
    return person


def _make_agent(db, person_id) -> CrmAgent:
    agent = CrmAgent(person_id=person_id, is_active=True)
    db.add(agent)
    db.flush()
    return agent


class TestAgentWeeklyPerformance:
    def test_returns_metrics_per_agent(self, db_session):
        """Returns agent metric dicts with expected keys."""
        from app.services.crm.reports import agent_weekly_performance

        agent_person = _make_person(db_session, name="Agent One")
        agent = _make_agent(db_session, agent_person.id)
        customer = _make_person(db_session, name="Customer One")

        now = datetime.now(UTC)
        conv = Conversation(
            person_id=customer.id,
            status=ConversationStatus.resolved,
            priority=ConversationPriority.medium,
            first_response_at=now - timedelta(hours=2),
            resolved_at=now - timedelta(hours=1),
            response_time_seconds=3600,
            resolution_time_seconds=7200,
        )
        db_session.add(conv)
        db_session.flush()

        assignment = ConversationAssignment(
            conversation_id=conv.id,
            agent_id=agent.id,
            is_active=True,
            assigned_at=now - timedelta(hours=3),
        )
        db_session.add(assignment)
        db_session.commit()

        start = now - timedelta(days=7)
        result = agent_weekly_performance(db_session, start_at=start, end_at=now)

        assert len(result) >= 1
        agent_row = next((r for r in result if r["agent_id"] == str(agent.id)), None)
        assert agent_row is not None
        assert agent_row["resolved_count"] == 1
        assert "median_response_seconds" in agent_row
        assert "median_resolution_seconds" in agent_row
        assert "open_backlog" in agent_row
        assert "sla_breach_count" in agent_row

    def test_empty_when_no_agents(self, db_session):
        """Returns empty list when no active agents exist."""
        from app.services.crm.reports import agent_weekly_performance

        now = datetime.now(UTC)
        result = agent_weekly_performance(db_session, start_at=now - timedelta(days=7), end_at=now)
        assert result == []
