"""Tests for DotMac ERP department/team sync (pull from ERP)."""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.models.person import Person
from app.models.service_team import ServiceTeam, ServiceTeamMember, ServiceTeamMemberRole, ServiceTeamType
from app.services.dotmac_erp.team_sync import (
    DotMacERPTeamSync,
    TeamSyncResult,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sync_service(db_session):
    """Team sync service with mocked client."""
    svc = DotMacERPTeamSync(db_session)
    svc._client = MagicMock()
    return svc


@pytest.fixture()
def team_person(db_session):
    """Person for team member resolution tests."""
    p = Person(
        first_name="Alice",
        last_name="Engineer",
        email=f"alice-{uuid.uuid4().hex[:6]}@company.com",
    )
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


def _make_department(
    dept_id="DEP-001",
    name="Fiber Team",
    dept_type="field_service",
    members=None,
    **overrides,
):
    return {
        "department_id": dept_id,
        "department_name": name,
        "department_type": dept_type,
        "region": "Western Cape",
        "is_active": True,
        "manager": None,
        "members": members or [],
        **overrides,
    }


def _make_member(employee_id="EMP-001", email="alice@company.com", role="member"):
    return {
        "employee_id": employee_id,
        "email": email,
        "full_name": "Alice Engineer",
        "role": role,
        "is_active": True,
    }


# ---------------------------------------------------------------------------
# TeamSyncResult
# ---------------------------------------------------------------------------


class TestTeamSyncResult:
    def test_defaults(self):
        r = TeamSyncResult()
        assert r.total_synced == 0
        assert r.has_errors is False

    def test_total_synced(self):
        r = TeamSyncResult(teams_created=2, teams_updated=1, members_added=5, members_updated=3)
        assert r.total_synced == 11

    def test_has_errors(self):
        r = TeamSyncResult(errors=[{"type": "api", "error": "fail"}])
        assert r.has_errors is True


# ---------------------------------------------------------------------------
# Team upsert
# ---------------------------------------------------------------------------


class TestUpsertTeam:
    def test_create_new_team(self, sync_service, db_session):
        dept = _make_department(dept_id="NEW-DEP", name="New Team")
        result = TeamSyncResult()
        team = sync_service._upsert_team(dept, result)
        db_session.flush()

        assert team is not None
        assert result.teams_created == 1
        found = db_session.query(ServiceTeam).filter(ServiceTeam.erp_department == "NEW-DEP").first()
        assert found.name == "New Team"
        assert found.team_type == ServiceTeamType.field_service

    def test_update_existing_team(self, sync_service, db_session):
        team = ServiceTeam(name="Old", team_type=ServiceTeamType.operations, erp_department="UPD-DEP")
        db_session.add(team)
        db_session.flush()

        dept = _make_department(dept_id="UPD-DEP", name="Updated", dept_type="support")
        result = TeamSyncResult()
        sync_service._upsert_team(dept, result)

        assert result.teams_updated == 1
        assert team.name == "Updated"
        assert team.team_type == ServiceTeamType.support

    def test_skip_without_department_id(self, sync_service):
        result = TeamSyncResult()
        team = sync_service._upsert_team({"department_name": "NoID"}, result)
        assert team is None
        assert result.teams_created == 0

    def test_team_type_mapping(self, sync_service, db_session):
        for erp_type, expected in [
            ("operations", ServiceTeamType.operations),
            ("support", ServiceTeamType.support),
            ("field_service", ServiceTeamType.field_service),
        ]:
            dept = _make_department(dept_id=f"TT-{erp_type}", dept_type=erp_type)
            result = TeamSyncResult()
            team = sync_service._upsert_team(dept, result)
            db_session.flush()
            assert team.team_type == expected

    def test_unknown_type_defaults_to_operations(self, sync_service, db_session):
        dept = _make_department(dept_id="TT-UNK", dept_type="unknown_type")
        result = TeamSyncResult()
        team = sync_service._upsert_team(dept, result)
        db_session.flush()
        assert team.team_type == ServiceTeamType.operations


# ---------------------------------------------------------------------------
# Member sync
# ---------------------------------------------------------------------------


class TestSyncTeamMembers:
    def test_add_member_by_email(self, sync_service, db_session, team_person):
        team = ServiceTeam(name="Test", team_type=ServiceTeamType.support, erp_department="MEM-001")
        db_session.add(team)
        db_session.flush()

        members = [_make_member(employee_id="NO-MATCH", email=team_person.email, role="member")]
        result = TeamSyncResult()
        sync_service._sync_team_members(team, members, result)
        db_session.flush()

        assert result.members_added == 1
        assert result.persons_matched == 1
        membership = (
            db_session.query(ServiceTeamMember)
            .filter(
                ServiceTeamMember.team_id == team.id,
                ServiceTeamMember.person_id == team_person.id,
            )
            .first()
        )
        assert membership is not None
        assert membership.role == ServiceTeamMemberRole.member

    def test_update_existing_member_role(self, sync_service, db_session, team_person):
        team = ServiceTeam(name="Test", team_type=ServiceTeamType.support, erp_department="MEM-002")
        db_session.add(team)
        db_session.flush()

        existing = ServiceTeamMember(team_id=team.id, person_id=team_person.id, role=ServiceTeamMemberRole.member)
        db_session.add(existing)
        db_session.flush()

        members = [_make_member(email=team_person.email, role="lead")]
        result = TeamSyncResult()
        sync_service._sync_team_members(team, members, result)

        assert result.members_updated == 1
        assert existing.role == ServiceTeamMemberRole.lead

    def test_deactivate_stale_members(self, sync_service, db_session, team_person):
        team = ServiceTeam(name="Test", team_type=ServiceTeamType.support, erp_department="MEM-003")
        db_session.add(team)
        db_session.flush()

        stale = ServiceTeamMember(
            team_id=team.id, person_id=team_person.id, role=ServiceTeamMemberRole.member, is_active=True
        )
        db_session.add(stale)
        db_session.flush()

        # Sync with empty member list → stale should be deactivated
        result = TeamSyncResult()
        sync_service._sync_team_members(team, [], result)

        assert result.members_deactivated == 1
        assert stale.is_active is False

    def test_skip_unresolvable_member(self, sync_service, db_session):
        team = ServiceTeam(name="Test", team_type=ServiceTeamType.support, erp_department="MEM-004")
        db_session.add(team)
        db_session.flush()

        members = [_make_member(employee_id="NO-MATCH", email="nonexistent@nope.com")]
        result = TeamSyncResult()
        sync_service._sync_team_members(team, members, result)

        assert result.persons_skipped == 1
        assert result.members_added == 0

    def test_inactive_member_skipped(self, sync_service, db_session, team_person):
        team = ServiceTeam(name="Test", team_type=ServiceTeamType.support, erp_department="MEM-005")
        db_session.add(team)
        db_session.flush()

        members = [
            {**_make_member(email=team_person.email), "is_active": False},
        ]
        result = TeamSyncResult()
        sync_service._sync_team_members(team, members, result)

        assert result.members_added == 0
        assert result.persons_matched == 0


# ---------------------------------------------------------------------------
# Full sync_departments flow
# ---------------------------------------------------------------------------


class TestSyncDepartments:
    def test_returns_error_when_not_configured(self, db_session):
        svc = DotMacERPTeamSync(db_session)
        with patch("app.services.dotmac_erp.team_sync.settings_spec") as mock_settings:
            mock_settings.resolve_value.return_value = None
            result = svc.sync_departments()
        assert result.has_errors
        assert result.errors[0]["type"] == "config"

    def test_full_sync_flow(self, sync_service, db_session, team_person):
        sync_service._client.get_departments.return_value = [
            _make_department(
                dept_id="FULL-001",
                name="Support",
                members=[_make_member(email=team_person.email, role="lead")],
            ),
        ]

        with patch("app.services.service_teams.sync_crm_agents"):
            result = sync_service.sync_departments()

        assert result.teams_created == 1
        assert result.members_added == 1
        assert result.persons_matched == 1
        assert result.has_errors is False

    def test_deactivate_stale_teams(self, sync_service, db_session):
        stale = ServiceTeam(name="Stale", team_type=ServiceTeamType.operations, erp_department="STALE-001")
        db_session.add(stale)
        db_session.commit()

        sync_service._client.get_departments.return_value = [
            _make_department(dept_id="ACTIVE-001"),
        ]

        with patch("app.services.service_teams.sync_crm_agents"):
            result = sync_service.sync_departments()

        assert result.teams_deactivated == 1
        db_session.refresh(stale)
        assert stale.is_active is False

    def test_pagination(self, sync_service, db_session):
        page1 = [_make_department(dept_id=f"PG-{i}") for i in range(500)]
        page2 = [_make_department(dept_id="PG-500")]
        sync_service._client.get_departments.side_effect = [page1, page2]

        with patch("app.services.service_teams.sync_crm_agents"):
            result = sync_service.sync_departments()

        assert result.teams_created == 501
        assert sync_service._client.get_departments.call_count == 2

    def test_error_per_department_is_isolated(self, sync_service, db_session):
        """An error in one department should not break the entire sync."""
        good = _make_department(dept_id="ISO-GOOD", name="Good")
        bad = _make_department(dept_id=None, name="Bad")  # no dept_id → skipped
        sync_service._client.get_departments.return_value = [good, bad]

        with patch("app.services.service_teams.sync_crm_agents"):
            result = sync_service.sync_departments()

        assert result.teams_created == 1
