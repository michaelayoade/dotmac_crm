import pytest
from fastapi import HTTPException

from app.models.service_team import ServiceTeamMemberRole, ServiceTeamType
from app.schemas.service_team import (
    ServiceTeamCreate,
    ServiceTeamMemberCreate,
    ServiceTeamMemberUpdate,
    ServiceTeamUpdate,
)
from app.services.service_teams import service_team_members, service_teams


def _make_team(db, name="Fiber Team", team_type=ServiceTeamType.field_service):
    return service_teams.create(
        db,
        ServiceTeamCreate(name=name, team_type=team_type),
    )


class TestServiceTeamsCRUD:
    def test_create(self, db_session):
        team = _make_team(db_session)
        assert team.name == "Fiber Team"
        assert team.team_type == ServiceTeamType.field_service
        assert team.is_active is True
        assert team.id is not None

    def test_get(self, db_session):
        team = _make_team(db_session)
        fetched = service_teams.get(db_session, str(team.id))
        assert fetched.id == team.id

    def test_get_not_found(self, db_session):
        with pytest.raises(HTTPException) as exc:
            service_teams.get(db_session, "00000000-0000-0000-0000-000000000000")
        assert exc.value.status_code == 404

    def test_list(self, db_session):
        _make_team(db_session, "Alpha")
        _make_team(db_session, "Beta", ServiceTeamType.support)
        items = service_teams.list(db_session)
        assert len(items) >= 2

    def test_list_with_search(self, db_session):
        _make_team(db_session, "Searchable Fiber")
        items = service_teams.list(db_session, search="Searchable")
        assert any(t.name == "Searchable Fiber" for t in items)

    def test_list_with_type_filter(self, db_session):
        _make_team(db_session, "Support T", ServiceTeamType.support)
        items = service_teams.list(db_session, team_type="support")
        assert all(t.team_type == ServiceTeamType.support for t in items)

    def test_update(self, db_session):
        team = _make_team(db_session)
        updated = service_teams.update(
            db_session,
            str(team.id),
            ServiceTeamUpdate(name="Updated Name"),
        )
        assert updated.name == "Updated Name"

    def test_delete_soft(self, db_session):
        team = _make_team(db_session)
        service_teams.delete(db_session, str(team.id))
        fetched = service_teams.get(db_session, str(team.id))
        assert fetched.is_active is False


class TestServiceTeamMembers:
    def test_add_member(self, db_session, person):
        team = _make_team(db_session)
        member = service_team_members.add_member(
            db_session,
            str(team.id),
            ServiceTeamMemberCreate(person_id=person.id),
        )
        assert member.team_id == team.id
        assert member.person_id == person.id
        assert member.role == ServiceTeamMemberRole.member

    def test_add_member_with_role(self, db_session, person):
        team = _make_team(db_session)
        member = service_team_members.add_member(
            db_session,
            str(team.id),
            ServiceTeamMemberCreate(person_id=person.id, role=ServiceTeamMemberRole.lead),
        )
        assert member.role == ServiceTeamMemberRole.lead

    def test_add_duplicate_member(self, db_session, person):
        team = _make_team(db_session)
        service_team_members.add_member(
            db_session,
            str(team.id),
            ServiceTeamMemberCreate(person_id=person.id),
        )
        with pytest.raises(HTTPException) as exc:
            service_team_members.add_member(
                db_session,
                str(team.id),
                ServiceTeamMemberCreate(person_id=person.id),
            )
        assert exc.value.status_code == 409

    def test_reactivate_inactive_member(self, db_session, person):
        team = _make_team(db_session)
        member = service_team_members.add_member(
            db_session,
            str(team.id),
            ServiceTeamMemberCreate(person_id=person.id),
        )
        service_team_members.remove_member(db_session, str(team.id), str(member.id))
        reactivated = service_team_members.add_member(
            db_session,
            str(team.id),
            ServiceTeamMemberCreate(person_id=person.id, role=ServiceTeamMemberRole.manager),
        )
        assert reactivated.is_active is True
        assert reactivated.role == ServiceTeamMemberRole.manager

    def test_remove_member(self, db_session, person):
        team = _make_team(db_session)
        member = service_team_members.add_member(
            db_session,
            str(team.id),
            ServiceTeamMemberCreate(person_id=person.id),
        )
        service_team_members.remove_member(db_session, str(team.id), str(member.id))
        members = service_team_members.list_members(db_session, str(team.id))
        assert all(m.is_active is True for m in members) or len(members) == 0

    def test_update_member(self, db_session, person):
        team = _make_team(db_session)
        member = service_team_members.add_member(
            db_session,
            str(team.id),
            ServiceTeamMemberCreate(person_id=person.id),
        )
        updated = service_team_members.update_member(
            db_session,
            str(team.id),
            str(member.id),
            ServiceTeamMemberUpdate(role=ServiceTeamMemberRole.manager),
        )
        assert updated.role == ServiceTeamMemberRole.manager

    def test_list_members(self, db_session, person):
        team = _make_team(db_session)
        service_team_members.add_member(
            db_session,
            str(team.id),
            ServiceTeamMemberCreate(person_id=person.id),
        )
        members = service_team_members.list_members(db_session, str(team.id))
        assert len(members) == 1

    def test_get_person_teams(self, db_session, person):
        team1 = _make_team(db_session, "Team A")
        team2 = _make_team(db_session, "Team B")
        service_team_members.add_member(
            db_session,
            str(team1.id),
            ServiceTeamMemberCreate(person_id=person.id),
        )
        service_team_members.add_member(
            db_session,
            str(team2.id),
            ServiceTeamMemberCreate(person_id=person.id),
        )
        memberships = service_team_members.get_person_teams(db_session, str(person.id))
        assert len(memberships) == 2
