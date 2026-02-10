"""Tests for CRM inbox 'my_team' assignment filter."""

import uuid

import pytest

from app.models.crm.conversation import Conversation, ConversationAssignment
from app.models.crm.enums import ConversationStatus
from app.models.crm.team import CrmTeam
from app.models.person import Person
from app.models.service_team import ServiceTeam, ServiceTeamMember, ServiceTeamMemberRole, ServiceTeamType
from app.services.crm.inbox.queries import list_inbox_conversations


def _unique_email():
    return f"test-{uuid.uuid4().hex[:8]}@example.com"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def agent_person(db_session):
    """Person who acts as an inbox agent."""
    p = Person(first_name="Agent", last_name="Smith", email=_unique_email())
    db_session.add(p)
    db_session.flush()
    return p


@pytest.fixture()
def contact_person(db_session):
    """Person who is the conversation contact."""
    p = Person(first_name="Customer", last_name="Jones", email=_unique_email())
    db_session.add(p)
    db_session.flush()
    return p


@pytest.fixture()
def linked_team(db_session, agent_person):
    """ServiceTeam + CrmTeam + membership for the agent."""
    st = ServiceTeam(name="Support", team_type=ServiceTeamType.support, region="Western Cape")
    db_session.add(st)
    db_session.flush()

    member = ServiceTeamMember(team_id=st.id, person_id=agent_person.id, role=ServiceTeamMemberRole.member)
    db_session.add(member)
    db_session.flush()

    crm_team = CrmTeam(name="CRM Support", service_team_id=st.id)
    db_session.add(crm_team)
    db_session.flush()

    return {"service_team": st, "crm_team": crm_team, "member": member}


@pytest.fixture()
def team_conversation(db_session, contact_person, linked_team):
    """Conversation assigned to the CRM team (via team_id)."""
    conv = Conversation(
        person_id=contact_person.id,
        status=ConversationStatus.open,
        subject="Team conversation",
    )
    db_session.add(conv)
    db_session.flush()

    assignment = ConversationAssignment(
        conversation_id=conv.id,
        team_id=linked_team["crm_team"].id,
        is_active=True,
    )
    db_session.add(assignment)
    db_session.flush()

    return conv


@pytest.fixture()
def unrelated_conversation(db_session, contact_person):
    """Conversation with no team assignment (should NOT appear in my_team)."""
    conv = Conversation(
        person_id=contact_person.id,
        status=ConversationStatus.open,
        subject="Unrelated conversation",
    )
    db_session.add(conv)
    db_session.flush()
    return conv


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMyTeamFilter:
    def test_returns_conversations_for_my_team(
        self, db_session, agent_person, team_conversation, unrelated_conversation
    ):
        """Agent should see conversations assigned to their team."""
        results = list_inbox_conversations(
            db_session,
            assignment="my_team",
            assigned_person_id=str(agent_person.id),
        )

        conv_ids = [r[0].id for r in results]
        assert team_conversation.id in conv_ids
        assert unrelated_conversation.id not in conv_ids

    def test_returns_empty_when_no_person_id(self, db_session, team_conversation):
        """Should return empty list when no assigned_person_id provided."""
        results = list_inbox_conversations(
            db_session,
            assignment="my_team",
            assigned_person_id=None,
        )
        assert results == []

    def test_returns_empty_when_person_not_in_any_team(
        self, db_session, team_conversation
    ):
        """Person not in any ServiceTeam should see no results."""
        outsider = Person(first_name="Outsider", last_name="X", email=_unique_email())
        db_session.add(outsider)
        db_session.flush()

        results = list_inbox_conversations(
            db_session,
            assignment="my_team",
            assigned_person_id=str(outsider.id),
        )
        assert results == []

    def test_inactive_membership_excluded(
        self, db_session, agent_person, linked_team, team_conversation
    ):
        """Deactivated team membership should exclude from my_team."""
        linked_team["member"].is_active = False
        db_session.flush()

        results = list_inbox_conversations(
            db_session,
            assignment="my_team",
            assigned_person_id=str(agent_person.id),
        )
        assert results == []

    def test_inactive_crm_team_excluded(
        self, db_session, agent_person, linked_team, team_conversation
    ):
        """Inactive CrmTeam should exclude from my_team results."""
        linked_team["crm_team"].is_active = False
        db_session.flush()

        results = list_inbox_conversations(
            db_session,
            assignment="my_team",
            assigned_person_id=str(agent_person.id),
        )
        assert results == []

    def test_inactive_assignment_excluded(
        self, db_session, agent_person, linked_team, contact_person
    ):
        """Inactive conversation assignment should not appear."""
        conv = Conversation(
            person_id=contact_person.id,
            status=ConversationStatus.open,
            subject="Inactive assignment",
        )
        db_session.add(conv)
        db_session.flush()

        assignment = ConversationAssignment(
            conversation_id=conv.id,
            team_id=linked_team["crm_team"].id,
            is_active=False,  # Deactivated
        )
        db_session.add(assignment)
        db_session.flush()

        results = list_inbox_conversations(
            db_session,
            assignment="my_team",
            assigned_person_id=str(agent_person.id),
        )
        conv_ids = [r[0].id for r in results]
        assert conv.id not in conv_ids

    def test_multiple_teams(self, db_session, agent_person, contact_person):
        """Agent in multiple service teams sees conversations from all linked CRM teams."""
        conversations = []
        for i in range(2):
            st = ServiceTeam(name=f"Team-{i}", team_type=ServiceTeamType.support)
            db_session.add(st)
            db_session.flush()

            db_session.add(ServiceTeamMember(
                team_id=st.id, person_id=agent_person.id, role=ServiceTeamMemberRole.member
            ))
            db_session.flush()

            crm_t = CrmTeam(name=f"CRM-{i}", service_team_id=st.id)
            db_session.add(crm_t)
            db_session.flush()

            conv = Conversation(
                person_id=contact_person.id,
                status=ConversationStatus.open,
                subject=f"Conv from team {i}",
            )
            db_session.add(conv)
            db_session.flush()

            db_session.add(ConversationAssignment(
                conversation_id=conv.id, team_id=crm_t.id, is_active=True
            ))
            db_session.flush()
            conversations.append(conv)

        results = list_inbox_conversations(
            db_session,
            assignment="my_team",
            assigned_person_id=str(agent_person.id),
        )
        conv_ids = {r[0].id for r in results}
        for conv in conversations:
            assert conv.id in conv_ids
