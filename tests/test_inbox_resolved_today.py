"""Tests for inbox resolved-today KPI."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.crm.conversation import Conversation
from app.models.crm.enums import ConversationStatus
from app.models.person import Person
from app.services.crm.inbox.queries import get_resolved_today_count


def test_get_resolved_today_count_uses_company_day_window(db_session):
    person = Person(first_name="Resolved", last_name="Today", email="resolved-today@example.com")
    db_session.add(person)
    db_session.flush()

    now = datetime.now(UTC).replace(microsecond=0)
    day_start = now.replace(hour=0, minute=0, second=0)

    included = Conversation(person_id=person.id, status=ConversationStatus.resolved, is_active=True)
    included.updated_at = day_start + timedelta(hours=2)
    excluded_before = Conversation(person_id=person.id, status=ConversationStatus.resolved, is_active=True)
    excluded_before.updated_at = day_start - timedelta(seconds=1)
    excluded_status = Conversation(person_id=person.id, status=ConversationStatus.open, is_active=True)
    excluded_status.updated_at = day_start + timedelta(hours=3)

    db_session.add_all([included, excluded_before, excluded_status])
    db_session.commit()

    assert get_resolved_today_count(db_session, timezone="UTC") == 1
