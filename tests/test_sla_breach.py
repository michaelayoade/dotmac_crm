"""Tests for SLA breach detection service."""

import uuid
from datetime import UTC, datetime, timedelta

from app.models.crm.conversation import Conversation
from app.models.crm.enums import ConversationPriority, ConversationStatus
from app.models.crm.team import CrmAgent
from app.models.person import Person
from app.services.crm.inbox.sla import (
    find_resolution_breaches,
    find_response_breaches,
    get_sla_targets,
)


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex}@example.com"


def _make_person(db) -> Person:
    person = Person(first_name="SLA", last_name="Test", email=_unique_email())
    db.add(person)
    db.flush()
    return person


def _make_conversation(
    db,
    person_id,
    *,
    priority=ConversationPriority.medium,
    created_at=None,
    status=ConversationStatus.open,
    first_response_at=None,
    resolved_at=None,
) -> Conversation:
    conv = Conversation(
        person_id=person_id,
        priority=priority,
        status=status,
        subject="Test SLA conversation",
        first_response_at=first_response_at,
        resolved_at=resolved_at,
        created_at=created_at or datetime.now(UTC),
    )
    db.add(conv)
    db.flush()
    return conv


def _make_agent(db, person_id) -> CrmAgent:
    agent = CrmAgent(person_id=person_id)
    db.add(agent)
    db.flush()
    return agent


# ---------------------------------------------------------------------------
# TestGetSlaTargets
# ---------------------------------------------------------------------------


class TestGetSlaTargets:
    def test_returns_default_targets(self, db_session):
        """get_sla_targets returns response and resolution dicts with int values."""
        targets = get_sla_targets(db_session)

        assert "response" in targets
        assert "resolution" in targets

        for key in ("urgent", "high", "medium", "low", "none"):
            assert key in targets["response"]
            assert key in targets["resolution"]
            assert isinstance(targets["response"][key], int)
            assert isinstance(targets["resolution"][key], int)

        # Verify specific defaults
        assert targets["response"]["urgent"] == 60
        assert targets["response"]["medium"] == 480
        assert targets["resolution"]["urgent"] == 240
        assert targets["resolution"]["medium"] == 2880


# ---------------------------------------------------------------------------
# TestFindResponseBreaches
# ---------------------------------------------------------------------------


class TestFindResponseBreaches:
    def test_detects_overdue_response(self, db_session):
        """An open conversation with no first_response_at past its SLA is breached."""
        person = _make_person(db_session)
        # Medium SLA = 480 min = 8h; created 10h ago -> breached
        created = datetime.now(UTC) - timedelta(hours=10)
        conv = _make_conversation(db_session, person.id, priority=ConversationPriority.medium, created_at=created)

        targets = {"urgent": 60, "high": 240, "medium": 480, "low": 1440, "none": 1440}
        breaches = find_response_breaches(db_session, targets)

        assert conv.id in [c.id for c in breaches]

    def test_ignores_responded_conversations(self, db_session):
        """A conversation with first_response_at set is not breached."""
        person = _make_person(db_session)
        created = datetime.now(UTC) - timedelta(hours=10)
        conv = _make_conversation(
            db_session,
            person.id,
            priority=ConversationPriority.medium,
            created_at=created,
            first_response_at=datetime.now(UTC) - timedelta(hours=5),
        )

        targets = {"urgent": 60, "high": 240, "medium": 480, "low": 1440, "none": 1440}
        breaches = find_response_breaches(db_session, targets)

        assert conv.id not in [c.id for c in breaches]

    def test_ignores_resolved_conversations(self, db_session):
        """A resolved conversation is not considered for response breach."""
        person = _make_person(db_session)
        created = datetime.now(UTC) - timedelta(hours=10)
        conv = _make_conversation(
            db_session,
            person.id,
            priority=ConversationPriority.medium,
            created_at=created,
            status=ConversationStatus.resolved,
        )

        targets = {"urgent": 60, "high": 240, "medium": 480, "low": 1440, "none": 1440}
        breaches = find_response_breaches(db_session, targets)

        assert conv.id not in [c.id for c in breaches]


# ---------------------------------------------------------------------------
# TestFindResolutionBreaches
# ---------------------------------------------------------------------------


class TestFindResolutionBreaches:
    def test_detects_overdue_resolution(self, db_session):
        """An open conversation past resolution SLA with no resolved_at is breached."""
        person = _make_person(db_session)
        # Medium resolution SLA = 2880 min = 48h; created 50h ago -> breached
        created = datetime.now(UTC) - timedelta(hours=50)
        conv = _make_conversation(
            db_session,
            person.id,
            priority=ConversationPriority.medium,
            created_at=created,
            first_response_at=datetime.now(UTC) - timedelta(hours=49),
        )

        targets = {"urgent": 240, "high": 1440, "medium": 2880, "low": 4320, "none": 4320}
        breaches = find_resolution_breaches(db_session, targets)

        assert conv.id in [c.id for c in breaches]
