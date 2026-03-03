from app.models.auth import AuthProvider, UserCredential
from app.models.crm.team import CrmAgent, CrmAgentTeam, CrmTeam
from app.models.person import Person
from app.services.crm.inbox.agents import list_active_agents_for_mentions, resolve_mentioned_person_ids_for_inbox


def test_crm_inbox_group_mentions_use_crm_team_members(db_session):
    person = Person(first_name="Helen", last_name="Desk", email="helpdesk@example.com", is_active=True)
    db_session.add(person)
    db_session.commit()
    db_session.refresh(person)

    db_session.add(
        UserCredential(
            person_id=person.id,
            provider=AuthProvider.local,
            username="helpdesk.agent",
            password_hash="not-a-real-hash",
            is_active=True,
        )
    )

    agent = CrmAgent(person_id=person.id, is_active=True)
    team = CrmTeam(name="Helpdesk", is_active=True)
    db_session.add_all([agent, team])
    db_session.commit()
    db_session.refresh(agent)
    db_session.refresh(team)

    db_session.add(CrmAgentTeam(agent_id=agent.id, team_id=team.id, is_active=True))
    db_session.commit()

    mention_options = list_active_agents_for_mentions(db_session)
    assert any(item["id"] == f"group:{team.id}" and item["kind"] == "group" for item in mention_options)

    recipient_person_ids = resolve_mentioned_person_ids_for_inbox(db_session, [f"group:{team.id}"])
    assert recipient_person_ids == [str(person.id)]
