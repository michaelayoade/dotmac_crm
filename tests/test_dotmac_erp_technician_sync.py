"""Tests for DotMac ERP technician sync (pull departments -> technicians)."""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.models.dispatch import TechnicianProfile
from app.models.person import Person
from app.services.dotmac_erp.technician_sync import (
    DotMacERPTechnicianSync,
    TechnicianSyncResult,
)


@pytest.fixture()
def sync_service(db_session):
    svc = DotMacERPTechnicianSync(db_session)
    svc._client = MagicMock()
    return svc


def _make_employee(
    employee_id="EMP-001",
    email="tech@example.com",
    full_name="Tech One",
    department="Projects",
    designation="Technician",
    is_active=True,
    **overrides,
):
    return {
        "employee_id": employee_id,
        "email": email,
        "full_name": full_name,
        # Note: department is added by the sync layer when it pulls from a department roster.
        "department": department,
        "designation": designation,
        "is_active": is_active,
        **overrides,
    }


def _make_department(name="Projects", members=None, **overrides):
    return {
        "department_id": "DEP-001",
        "department_name": name,
        "department_type": "operations",
        "region": None,
        "is_active": True,
        "manager": None,
        "members": members or [],
        **overrides,
    }


class TestTechnicianSyncResult:
    def test_defaults(self):
        r = TechnicianSyncResult()
        assert r.total_synced == 0
        assert r.has_errors is False

    def test_has_errors(self):
        r = TechnicianSyncResult(errors=[{"type": "x"}])
        assert r.has_errors is True


class TestSyncAll:
    def test_returns_error_when_not_configured(self, db_session):
        svc = DotMacERPTechnicianSync(db_session)
        with patch("app.services.dotmac_erp.technician_sync.settings_spec") as mock_settings:
            mock_settings.resolve_value.return_value = None
            result = svc.sync_all()
        assert result.has_errors
        assert result.errors[0]["type"] == "config"

    def test_creates_person_and_technician_for_projects_employee(self, sync_service, db_session):
        email = f"tech-{uuid.uuid4().hex[:6]}@example.com"
        sync_service._client.get_departments.return_value = [_make_department(members=[_make_employee(email=email)])]

        result = sync_service.sync_all()

        assert result.persons_created == 1
        assert result.technicians_created == 1
        person = db_session.query(Person).filter(Person.email == email).first()
        assert person is not None
        tech = db_session.query(TechnicianProfile).filter(TechnicianProfile.person_id == person.id).first()
        assert tech is not None
        assert tech.is_active is True
        assert tech.erp_employee_id == "EMP-001"

    def test_skips_non_projects_department(self, sync_service, db_session):
        email = f"nontech-{uuid.uuid4().hex[:6]}@example.com"
        sync_service._client.get_departments.return_value = [
            _make_department(name="Sales", members=[_make_employee(email=email, department="Sales")])
        ]

        result = sync_service.sync_all()

        assert result.technicians_created == 0
        assert db_session.query(Person).filter(Person.email == email).first() is None

    def test_links_existing_person_by_email(self, sync_service, db_session):
        email = f"existing-{uuid.uuid4().hex[:6]}@example.com"
        person = Person(first_name="Existing", last_name="Person", email=email)
        db_session.add(person)
        db_session.flush()

        sync_service._client.get_departments.return_value = [
            _make_department(members=[_make_employee(email=email, employee_id="EMP-LINK")])
        ]
        result = sync_service.sync_all()

        assert result.persons_created == 0
        assert result.technicians_created == 1
        tech = db_session.query(TechnicianProfile).filter(TechnicianProfile.person_id == person.id).first()
        assert tech is not None
        assert tech.erp_employee_id == "EMP-LINK"

    def test_deactivates_erp_linked_technician_no_longer_in_projects(self, sync_service, db_session):
        # Existing linked technician
        person = Person(first_name="Old", last_name="Tech", email=f"old-{uuid.uuid4().hex[:6]}@example.com")
        db_session.add(person)
        db_session.flush()
        tech = TechnicianProfile(person_id=person.id, erp_employee_id="EMP-OLD", is_active=True)
        db_session.add(tech)
        db_session.commit()

        # ERP now says this employee is not in Projects
        sync_service._client.get_departments.return_value = [
            _make_department(
                name="Support",
                members=[_make_employee(employee_id="EMP-OLD", email=person.email, department="Support")],
            )
        ]
        result = sync_service.sync_all()

        assert result.technicians_deactivated == 1
        db_session.refresh(tech)
        assert tech.is_active is False
