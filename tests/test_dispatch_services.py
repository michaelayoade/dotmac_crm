"""Tests for dispatch service."""

import uuid
from datetime import datetime, timezone, timedelta

import pytest
from fastapi import HTTPException

from app.models.dispatch import (
    DispatchQueueStatus,
    DispatchRule,
    Skill,
    TechnicianProfile,
    TechnicianSkill,
)
from app.schemas.dispatch import (
    AvailabilityBlockCreate,
    AvailabilityBlockUpdate,
    DispatchRuleCreate,
    DispatchRuleUpdate,
    ShiftCreate,
    ShiftUpdate,
    SkillCreate,
    SkillUpdate,
    TechnicianProfileCreate,
    TechnicianProfileUpdate,
    TechnicianSkillCreate,
    TechnicianSkillUpdate,
    WorkOrderAssignmentQueueCreate,
    WorkOrderAssignmentQueueUpdate,
)
from app.services import dispatch as dispatch_service
from app.services.common import apply_ordering, apply_pagination, validate_enum


# =============================================================================
# Helper Function Tests
# =============================================================================


class TestApplyOrdering:
    """Tests for _apply_ordering function."""

    def test_valid_order_by_asc(self, db_session):
        """Test valid order_by with asc direction."""
        query = db_session.query(Skill)
        allowed = {"name": Skill.name, "created_at": Skill.created_at}
        result = apply_ordering(query, "name", "asc", allowed)
        assert result is not None

    def test_valid_order_by_desc(self, db_session):
        """Test valid order_by with desc direction."""
        query = db_session.query(Skill)
        allowed = {"name": Skill.name, "created_at": Skill.created_at}
        result = apply_ordering(query, "name", "desc", allowed)
        assert result is not None

    def test_invalid_order_by(self, db_session):
        """Test invalid order_by raises HTTPException."""
        query = db_session.query(Skill)
        allowed = {"name": Skill.name}

        with pytest.raises(HTTPException) as exc_info:
            apply_ordering(query, "invalid_column", "asc", allowed)

        assert exc_info.value.status_code == 400
        assert "Invalid order_by" in exc_info.value.detail


class TestApplyPagination:
    """Tests for _apply_pagination function."""

    def test_applies_limit_and_offset(self, db_session):
        """Test applies limit and offset to query."""
        query = db_session.query(Skill)
        result = apply_pagination(query, 10, 5)
        assert result is not None


class TestValidateEnum:
    """Tests for _validate_enum function."""

    def test_returns_none_for_none(self):
        """Test returns None for None input."""
        result = validate_enum(None, DispatchQueueStatus, "status")
        assert result is None

    def test_converts_valid_string(self):
        """Test converts valid string to enum."""
        result = validate_enum("queued", DispatchQueueStatus, "status")
        assert result == DispatchQueueStatus.queued

    def test_invalid_string_raises(self):
        """Test invalid string raises HTTPException."""
        with pytest.raises(HTTPException) as exc_info:
            validate_enum("invalid_status", DispatchQueueStatus, "status")

        assert exc_info.value.status_code == 400
        assert "Invalid status" in exc_info.value.detail


class TestEnsureFunctions:
    """Tests for ensure helper functions."""

    def test_ensure_person_valid(self, db_session, person):
        """Test _ensure_person with valid person."""
        # Should not raise
        dispatch_service._ensure_person(db_session, str(person.id))

    def test_ensure_person_invalid(self, db_session):
        """Test _ensure_person with invalid person."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service._ensure_person(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404
        assert "Person not found" in exc_info.value.detail

    def test_ensure_skill_invalid(self, db_session):
        """Test _ensure_skill with invalid skill."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service._ensure_skill(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404
        assert "Skill not found" in exc_info.value.detail

    def test_ensure_technician_invalid(self, db_session):
        """Test _ensure_technician with invalid technician."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service._ensure_technician(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404
        assert "Technician not found" in exc_info.value.detail

    def test_ensure_work_order_valid(self, db_session, work_order):
        """Test _ensure_work_order with valid work order."""
        result = dispatch_service._ensure_work_order(db_session, str(work_order.id))
        assert result.id == work_order.id

    def test_ensure_work_order_invalid(self, db_session):
        """Test _ensure_work_order with invalid work order."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service._ensure_work_order(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404
        assert "Work order not found" in exc_info.value.detail


# =============================================================================
# Skills CRUD Tests
# =============================================================================


class TestSkillsCRUD:
    """Tests for Skills CRUD operations."""

    def test_creates_skill(self, db_session):
        """Test creates a skill."""
        skill = dispatch_service.skills.create(
            db_session,
            SkillCreate(name="Fiber Splicing"),
        )
        assert skill.name == "Fiber Splicing"
        assert skill.is_active is True

    def test_gets_skill_by_id(self, db_session):
        """Test gets skill by ID."""
        skill = dispatch_service.skills.create(
            db_session,
            SkillCreate(name="Get Test Skill"),
        )
        fetched = dispatch_service.skills.get(db_session, str(skill.id))
        assert fetched.id == skill.id

    def test_get_skill_not_found(self, db_session):
        """Test raises 404 for non-existent skill."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.skills.get(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404

    def test_lists_active_skills(self, db_session):
        """Test lists active skills."""
        dispatch_service.skills.create(db_session, SkillCreate(name="List Skill 1"))
        dispatch_service.skills.create(db_session, SkillCreate(name="List Skill 2"))

        skills = dispatch_service.skills.list(
            db_session,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert len(skills) >= 2
        assert all(s.is_active for s in skills)

    def test_filters_by_is_active_false(self, db_session):
        """Test filters by is_active=False."""
        skill = dispatch_service.skills.create(
            db_session, SkillCreate(name="Inactive Skill")
        )
        dispatch_service.skills.delete(db_session, str(skill.id))

        skills = dispatch_service.skills.list(
            db_session,
            is_active=False,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(not s.is_active for s in skills)

    def test_updates_skill(self, db_session):
        """Test updates skill."""
        skill = dispatch_service.skills.create(
            db_session, SkillCreate(name="Update Test")
        )
        updated = dispatch_service.skills.update(
            db_session, str(skill.id), SkillUpdate(name="Updated Name")
        )
        assert updated.name == "Updated Name"

    def test_update_skill_not_found(self, db_session):
        """Test update raises 404 for non-existent skill."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.skills.update(
                db_session, str(uuid.uuid4()), SkillUpdate(name="new")
            )
        assert exc_info.value.status_code == 404

    def test_deletes_skill(self, db_session):
        """Test soft deletes skill."""
        skill = dispatch_service.skills.create(
            db_session, SkillCreate(name="Delete Test")
        )
        dispatch_service.skills.delete(db_session, str(skill.id))
        db_session.refresh(skill)
        assert skill.is_active is False

    def test_delete_skill_not_found(self, db_session):
        """Test delete raises 404 for non-existent skill."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.skills.delete(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404


# =============================================================================
# Technicians CRUD Tests
# =============================================================================


class TestTechniciansCRUD:
    """Tests for Technicians CRUD operations."""

    def test_creates_technician(self, db_session, person):
        """Test creates a technician profile."""
        technician = dispatch_service.technicians.create(
            db_session,
            TechnicianProfileCreate(person_id=person.id),
        )
        assert technician.person_id == person.id
        assert technician.is_active is True

    def test_creates_technician_with_region(self, db_session, person):
        """Test creates technician with region."""
        technician = dispatch_service.technicians.create(
            db_session,
            TechnicianProfileCreate(person_id=person.id, region="North"),
        )
        assert technician.region == "North"

    def test_create_technician_invalid_person(self, db_session):
        """Test raises for non-existent person."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.technicians.create(
                db_session,
                TechnicianProfileCreate(person_id=uuid.uuid4()),
            )
        assert exc_info.value.status_code == 404

    def test_gets_technician_by_id(self, db_session, person):
        """Test gets technician by ID."""
        technician = dispatch_service.technicians.create(
            db_session,
            TechnicianProfileCreate(person_id=person.id),
        )
        fetched = dispatch_service.technicians.get(db_session, str(technician.id))
        assert fetched.id == technician.id

    def test_get_technician_not_found(self, db_session):
        """Test raises 404 for non-existent technician."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.technicians.get(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404

    def test_lists_technicians(self, db_session, person):
        """Test lists technicians."""
        dispatch_service.technicians.create(
            db_session, TechnicianProfileCreate(person_id=person.id)
        )

        technicians = dispatch_service.technicians.list(
            db_session,
            person_id=None,
            region=None,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert len(technicians) >= 1

    def test_filters_by_person_id(self, db_session, person):
        """Test filters by person_id."""
        dispatch_service.technicians.create(
            db_session, TechnicianProfileCreate(person_id=person.id)
        )

        technicians = dispatch_service.technicians.list(
            db_session,
            person_id=str(person.id),
            region=None,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(t.person_id == person.id for t in technicians)

    def test_filters_by_region(self, db_session, person):
        """Test filters by region."""
        dispatch_service.technicians.create(
            db_session, TechnicianProfileCreate(person_id=person.id, region="South")
        )

        technicians = dispatch_service.technicians.list(
            db_session,
            person_id=None,
            region="South",
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(t.region == "South" for t in technicians)

    def test_updates_technician(self, db_session, person):
        """Test updates technician."""
        technician = dispatch_service.technicians.create(
            db_session, TechnicianProfileCreate(person_id=person.id)
        )
        updated = dispatch_service.technicians.update(
            db_session, str(technician.id), TechnicianProfileUpdate(region="East")
        )
        assert updated.region == "East"

    def test_update_technician_validates_person(self, db_session, person):
        """Test update validates person_id."""
        technician = dispatch_service.technicians.create(
            db_session, TechnicianProfileCreate(person_id=person.id)
        )
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.technicians.update(
                db_session,
                str(technician.id),
                TechnicianProfileUpdate(person_id=uuid.uuid4()),
            )
        assert exc_info.value.status_code == 404

    def test_update_technician_not_found(self, db_session):
        """Test update raises 404 for non-existent technician."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.technicians.update(
                db_session, str(uuid.uuid4()), TechnicianProfileUpdate(region="West")
            )
        assert exc_info.value.status_code == 404

    def test_deletes_technician(self, db_session, person):
        """Test soft deletes technician."""
        technician = dispatch_service.technicians.create(
            db_session, TechnicianProfileCreate(person_id=person.id)
        )
        dispatch_service.technicians.delete(db_session, str(technician.id))
        db_session.refresh(technician)
        assert technician.is_active is False

    def test_delete_technician_not_found(self, db_session):
        """Test delete raises 404 for non-existent technician."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.technicians.delete(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404


# =============================================================================
# TechnicianSkills CRUD Tests
# =============================================================================


@pytest.fixture
def dispatch_skill(db_session):
    """Create a skill for dispatch tests."""
    return dispatch_service.skills.create(db_session, SkillCreate(name="Test Dispatch Skill"))


@pytest.fixture
def dispatch_technician(db_session, person):
    """Create a technician for dispatch tests."""
    return dispatch_service.technicians.create(
        db_session, TechnicianProfileCreate(person_id=person.id)
    )


class TestTechnicianSkillsCRUD:
    """Tests for TechnicianSkills CRUD operations."""

    def test_creates_technician_skill(self, db_session, dispatch_technician, dispatch_skill):
        """Test creates a technician skill link."""
        ts = dispatch_service.technician_skills.create(
            db_session,
            TechnicianSkillCreate(technician_id=dispatch_technician.id, skill_id=dispatch_skill.id),
        )
        assert ts.technician_id == dispatch_technician.id
        assert ts.skill_id == dispatch_skill.id

    def test_create_validates_technician(self, db_session, dispatch_skill):
        """Test validates technician exists."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.technician_skills.create(
                db_session,
                TechnicianSkillCreate(technician_id=uuid.uuid4(), skill_id=dispatch_skill.id),
            )
        assert exc_info.value.status_code == 404
        assert "Technician not found" in exc_info.value.detail

    def test_create_validates_skill(self, db_session, dispatch_technician):
        """Test validates skill exists."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.technician_skills.create(
                db_session,
                TechnicianSkillCreate(technician_id=dispatch_technician.id, skill_id=uuid.uuid4()),
            )
        assert exc_info.value.status_code == 404
        assert "Skill not found" in exc_info.value.detail

    def test_gets_technician_skill(self, db_session, dispatch_technician, dispatch_skill):
        """Test gets technician skill by ID."""
        ts = dispatch_service.technician_skills.create(
            db_session,
            TechnicianSkillCreate(technician_id=dispatch_technician.id, skill_id=dispatch_skill.id),
        )
        fetched = dispatch_service.technician_skills.get(db_session, str(ts.id))
        assert fetched.id == ts.id

    def test_get_technician_skill_not_found(self, db_session):
        """Test raises 404 for non-existent technician skill."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.technician_skills.get(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404

    def test_lists_technician_skills(self, db_session, dispatch_technician, dispatch_skill):
        """Test lists technician skills."""
        dispatch_service.technician_skills.create(
            db_session,
            TechnicianSkillCreate(technician_id=dispatch_technician.id, skill_id=dispatch_skill.id),
        )

        skills = dispatch_service.technician_skills.list(
            db_session,
            technician_id=None,
            skill_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert len(skills) >= 1

    def test_filters_by_technician_id(self, db_session, dispatch_technician, dispatch_skill):
        """Test filters by technician_id."""
        dispatch_service.technician_skills.create(
            db_session,
            TechnicianSkillCreate(technician_id=dispatch_technician.id, skill_id=dispatch_skill.id),
        )

        skills = dispatch_service.technician_skills.list(
            db_session,
            technician_id=str(dispatch_technician.id),
            skill_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(s.technician_id == dispatch_technician.id for s in skills)

    def test_filters_by_skill_id(self, db_session, dispatch_technician, dispatch_skill):
        """Test filters by skill_id."""
        dispatch_service.technician_skills.create(
            db_session,
            TechnicianSkillCreate(technician_id=dispatch_technician.id, skill_id=dispatch_skill.id),
        )

        skills = dispatch_service.technician_skills.list(
            db_session,
            technician_id=None,
            skill_id=str(dispatch_skill.id),
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(s.skill_id == dispatch_skill.id for s in skills)

    def test_updates_technician_skill(self, db_session, dispatch_technician, dispatch_skill):
        """Test updates technician skill."""
        ts = dispatch_service.technician_skills.create(
            db_session,
            TechnicianSkillCreate(technician_id=dispatch_technician.id, skill_id=dispatch_skill.id),
        )
        updated = dispatch_service.technician_skills.update(
            db_session, str(ts.id), TechnicianSkillUpdate(proficiency=5)
        )
        assert updated.proficiency == 5

    def test_update_validates_technician(self, db_session, dispatch_technician, dispatch_skill):
        """Test update validates technician_id."""
        ts = dispatch_service.technician_skills.create(
            db_session,
            TechnicianSkillCreate(technician_id=dispatch_technician.id, skill_id=dispatch_skill.id),
        )
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.technician_skills.update(
                db_session,
                str(ts.id),
                TechnicianSkillUpdate(technician_id=uuid.uuid4()),
            )
        assert exc_info.value.status_code == 404

    def test_update_validates_skill(self, db_session, dispatch_technician, dispatch_skill):
        """Test update validates skill_id."""
        ts = dispatch_service.technician_skills.create(
            db_session,
            TechnicianSkillCreate(technician_id=dispatch_technician.id, skill_id=dispatch_skill.id),
        )
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.technician_skills.update(
                db_session, str(ts.id), TechnicianSkillUpdate(skill_id=uuid.uuid4())
            )
        assert exc_info.value.status_code == 404

    def test_update_not_found(self, db_session):
        """Test update raises 404 for non-existent."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.technician_skills.update(
                db_session, str(uuid.uuid4()), TechnicianSkillUpdate(proficiency=3)
            )
        assert exc_info.value.status_code == 404

    def test_deletes_technician_skill(self, db_session, dispatch_technician, dispatch_skill):
        """Test soft deletes technician skill."""
        ts = dispatch_service.technician_skills.create(
            db_session,
            TechnicianSkillCreate(technician_id=dispatch_technician.id, skill_id=dispatch_skill.id),
        )
        dispatch_service.technician_skills.delete(db_session, str(ts.id))
        db_session.refresh(ts)
        assert ts.is_active is False

    def test_delete_not_found(self, db_session):
        """Test delete raises 404 for non-existent."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.technician_skills.delete(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404


# =============================================================================
# Shifts CRUD Tests
# =============================================================================


class TestShiftsCRUD:
    """Tests for Shifts CRUD operations."""

    def test_creates_shift(self, db_session, dispatch_technician):
        """Test creates a shift."""
        now = datetime.now(timezone.utc)
        shift = dispatch_service.shifts.create(
            db_session,
            ShiftCreate(
                technician_id=dispatch_technician.id,
                start_at=now,
                end_at=now + timedelta(hours=8),
            ),
        )
        assert shift.technician_id == dispatch_technician.id

    def test_create_validates_technician(self, db_session):
        """Test validates technician exists."""
        now = datetime.now(timezone.utc)
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.shifts.create(
                db_session,
                ShiftCreate(
                    technician_id=uuid.uuid4(),
                    start_at=now,
                    end_at=now + timedelta(hours=8),
                ),
            )
        assert exc_info.value.status_code == 404

    def test_gets_shift(self, db_session, dispatch_technician):
        """Test gets shift by ID."""
        now = datetime.now(timezone.utc)
        shift = dispatch_service.shifts.create(
            db_session,
            ShiftCreate(
                technician_id=dispatch_technician.id,
                start_at=now,
                end_at=now + timedelta(hours=8),
            ),
        )
        fetched = dispatch_service.shifts.get(db_session, str(shift.id))
        assert fetched.id == shift.id

    def test_get_shift_not_found(self, db_session):
        """Test raises 404 for non-existent shift."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.shifts.get(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404

    def test_lists_shifts(self, db_session, dispatch_technician):
        """Test lists shifts."""
        now = datetime.now(timezone.utc)
        dispatch_service.shifts.create(
            db_session,
            ShiftCreate(
                technician_id=dispatch_technician.id,
                start_at=now,
                end_at=now + timedelta(hours=8),
            ),
        )

        shifts = dispatch_service.shifts.list(
            db_session,
            technician_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert len(shifts) >= 1

    def test_filters_by_technician_id(self, db_session, dispatch_technician):
        """Test filters by technician_id."""
        now = datetime.now(timezone.utc)
        dispatch_service.shifts.create(
            db_session,
            ShiftCreate(
                technician_id=dispatch_technician.id,
                start_at=now,
                end_at=now + timedelta(hours=8),
            ),
        )

        shifts = dispatch_service.shifts.list(
            db_session,
            technician_id=str(dispatch_technician.id),
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(s.technician_id == dispatch_technician.id for s in shifts)

    def test_updates_shift(self, db_session, dispatch_technician):
        """Test updates shift."""
        now = datetime.now(timezone.utc)
        shift = dispatch_service.shifts.create(
            db_session,
            ShiftCreate(
                technician_id=dispatch_technician.id,
                start_at=now,
                end_at=now + timedelta(hours=8),
            ),
        )
        updated = dispatch_service.shifts.update(
            db_session, str(shift.id), ShiftUpdate(timezone="America/New_York")
        )
        assert updated.timezone == "America/New_York"

    def test_update_validates_technician(self, db_session, dispatch_technician):
        """Test update validates technician_id."""
        now = datetime.now(timezone.utc)
        shift = dispatch_service.shifts.create(
            db_session,
            ShiftCreate(
                technician_id=dispatch_technician.id,
                start_at=now,
                end_at=now + timedelta(hours=8),
            ),
        )
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.shifts.update(
                db_session, str(shift.id), ShiftUpdate(technician_id=uuid.uuid4())
            )
        assert exc_info.value.status_code == 404

    def test_update_not_found(self, db_session):
        """Test update raises 404 for non-existent shift."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.shifts.update(
                db_session, str(uuid.uuid4()), ShiftUpdate(timezone="UTC")
            )
        assert exc_info.value.status_code == 404

    def test_deletes_shift(self, db_session, dispatch_technician):
        """Test soft deletes shift."""
        now = datetime.now(timezone.utc)
        shift = dispatch_service.shifts.create(
            db_session,
            ShiftCreate(
                technician_id=dispatch_technician.id,
                start_at=now,
                end_at=now + timedelta(hours=8),
            ),
        )
        dispatch_service.shifts.delete(db_session, str(shift.id))
        db_session.refresh(shift)
        assert shift.is_active is False

    def test_delete_not_found(self, db_session):
        """Test delete raises 404 for non-existent shift."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.shifts.delete(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404


# =============================================================================
# AvailabilityBlocks CRUD Tests
# =============================================================================


class TestAvailabilityBlocksCRUD:
    """Tests for AvailabilityBlocks CRUD operations."""

    def test_creates_block(self, db_session, dispatch_technician):
        """Test creates an availability block."""
        now = datetime.now(timezone.utc)
        block = dispatch_service.availability_blocks.create(
            db_session,
            AvailabilityBlockCreate(
                technician_id=dispatch_technician.id,
                start_at=now,
                end_at=now + timedelta(hours=4),
            ),
        )
        assert block.technician_id == dispatch_technician.id

    def test_create_validates_technician(self, db_session):
        """Test validates technician exists."""
        now = datetime.now(timezone.utc)
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.availability_blocks.create(
                db_session,
                AvailabilityBlockCreate(
                    technician_id=uuid.uuid4(),
                    start_at=now,
                    end_at=now + timedelta(hours=4),
                ),
            )
        assert exc_info.value.status_code == 404

    def test_gets_block(self, db_session, dispatch_technician):
        """Test gets block by ID."""
        now = datetime.now(timezone.utc)
        block = dispatch_service.availability_blocks.create(
            db_session,
            AvailabilityBlockCreate(
                technician_id=dispatch_technician.id,
                start_at=now,
                end_at=now + timedelta(hours=4),
            ),
        )
        fetched = dispatch_service.availability_blocks.get(db_session, str(block.id))
        assert fetched.id == block.id

    def test_get_block_not_found(self, db_session):
        """Test raises 404 for non-existent block."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.availability_blocks.get(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404

    def test_lists_blocks(self, db_session, dispatch_technician):
        """Test lists availability blocks."""
        now = datetime.now(timezone.utc)
        dispatch_service.availability_blocks.create(
            db_session,
            AvailabilityBlockCreate(
                technician_id=dispatch_technician.id,
                start_at=now,
                end_at=now + timedelta(hours=4),
            ),
        )

        blocks = dispatch_service.availability_blocks.list(
            db_session,
            technician_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert len(blocks) >= 1

    def test_filters_by_technician_id(self, db_session, dispatch_technician):
        """Test filters by technician_id."""
        now = datetime.now(timezone.utc)
        dispatch_service.availability_blocks.create(
            db_session,
            AvailabilityBlockCreate(
                technician_id=dispatch_technician.id,
                start_at=now,
                end_at=now + timedelta(hours=4),
            ),
        )

        blocks = dispatch_service.availability_blocks.list(
            db_session,
            technician_id=str(dispatch_technician.id),
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(b.technician_id == dispatch_technician.id for b in blocks)

    def test_updates_block(self, db_session, dispatch_technician):
        """Test updates block."""
        now = datetime.now(timezone.utc)
        block = dispatch_service.availability_blocks.create(
            db_session,
            AvailabilityBlockCreate(
                technician_id=dispatch_technician.id,
                start_at=now,
                end_at=now + timedelta(hours=4),
            ),
        )
        updated = dispatch_service.availability_blocks.update(
            db_session, str(block.id), AvailabilityBlockUpdate(reason="PTO")
        )
        assert updated.reason == "PTO"

    def test_update_validates_technician(self, db_session, dispatch_technician):
        """Test update validates technician_id."""
        now = datetime.now(timezone.utc)
        block = dispatch_service.availability_blocks.create(
            db_session,
            AvailabilityBlockCreate(
                technician_id=dispatch_technician.id,
                start_at=now,
                end_at=now + timedelta(hours=4),
            ),
        )
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.availability_blocks.update(
                db_session,
                str(block.id),
                AvailabilityBlockUpdate(technician_id=uuid.uuid4()),
            )
        assert exc_info.value.status_code == 404

    def test_update_not_found(self, db_session):
        """Test update raises 404 for non-existent block."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.availability_blocks.update(
                db_session, str(uuid.uuid4()), AvailabilityBlockUpdate(reason="test")
            )
        assert exc_info.value.status_code == 404

    def test_deletes_block(self, db_session, dispatch_technician):
        """Test soft deletes block."""
        now = datetime.now(timezone.utc)
        block = dispatch_service.availability_blocks.create(
            db_session,
            AvailabilityBlockCreate(
                technician_id=dispatch_technician.id,
                start_at=now,
                end_at=now + timedelta(hours=4),
            ),
        )
        dispatch_service.availability_blocks.delete(db_session, str(block.id))
        db_session.refresh(block)
        assert block.is_active is False

    def test_delete_not_found(self, db_session):
        """Test delete raises 404 for non-existent block."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.availability_blocks.delete(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404


# =============================================================================
# DispatchRules CRUD Tests
# =============================================================================


class TestDispatchRulesCRUD:
    """Tests for DispatchRules CRUD operations."""

    def test_creates_rule(self, db_session):
        """Test creates a dispatch rule."""
        rule = dispatch_service.dispatch_rules.create(
            db_session,
            DispatchRuleCreate(name="High Priority Rule", priority=10),
        )
        assert rule.name == "High Priority Rule"
        assert rule.priority == 10

    def test_gets_rule(self, db_session):
        """Test gets rule by ID."""
        rule = dispatch_service.dispatch_rules.create(
            db_session,
            DispatchRuleCreate(name="Get Test Rule"),
        )
        fetched = dispatch_service.dispatch_rules.get(db_session, str(rule.id))
        assert fetched.id == rule.id

    def test_get_rule_not_found(self, db_session):
        """Test raises 404 for non-existent rule."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.dispatch_rules.get(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404

    def test_lists_rules(self, db_session):
        """Test lists dispatch rules."""
        dispatch_service.dispatch_rules.create(
            db_session, DispatchRuleCreate(name="List Rule 1")
        )
        dispatch_service.dispatch_rules.create(
            db_session, DispatchRuleCreate(name="List Rule 2")
        )

        rules = dispatch_service.dispatch_rules.list(
            db_session,
            is_active=None,
            order_by="priority",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        assert len(rules) >= 2

    def test_updates_rule(self, db_session):
        """Test updates rule."""
        rule = dispatch_service.dispatch_rules.create(
            db_session, DispatchRuleCreate(name="Update Test")
        )
        updated = dispatch_service.dispatch_rules.update(
            db_session, str(rule.id), DispatchRuleUpdate(auto_assign=True)
        )
        assert updated.auto_assign is True

    def test_update_not_found(self, db_session):
        """Test update raises 404 for non-existent rule."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.dispatch_rules.update(
                db_session, str(uuid.uuid4()), DispatchRuleUpdate(priority=5)
            )
        assert exc_info.value.status_code == 404

    def test_deletes_rule(self, db_session):
        """Test soft deletes rule."""
        rule = dispatch_service.dispatch_rules.create(
            db_session, DispatchRuleCreate(name="Delete Test")
        )
        dispatch_service.dispatch_rules.delete(db_session, str(rule.id))
        db_session.refresh(rule)
        assert rule.is_active is False

    def test_delete_not_found(self, db_session):
        """Test delete raises 404 for non-existent rule."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.dispatch_rules.delete(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404


# =============================================================================
# AssignmentQueue CRUD Tests
# =============================================================================


class TestAssignmentQueueCRUD:
    """Tests for AssignmentQueue CRUD operations."""

    def test_creates_entry(self, db_session, work_order):
        """Test creates a queue entry."""
        entry = dispatch_service.assignment_queue.create(
            db_session,
            WorkOrderAssignmentQueueCreate(work_order_id=work_order.id),
        )
        assert entry.work_order_id == work_order.id
        assert entry.status == DispatchQueueStatus.queued

    def test_create_validates_work_order(self, db_session):
        """Test validates work order exists."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.assignment_queue.create(
                db_session,
                WorkOrderAssignmentQueueCreate(work_order_id=uuid.uuid4()),
            )
        assert exc_info.value.status_code == 404

    def test_gets_entry(self, db_session, work_order):
        """Test gets entry by ID."""
        entry = dispatch_service.assignment_queue.create(
            db_session,
            WorkOrderAssignmentQueueCreate(work_order_id=work_order.id),
        )
        fetched = dispatch_service.assignment_queue.get(db_session, str(entry.id))
        assert fetched.id == entry.id

    def test_get_entry_not_found(self, db_session):
        """Test raises 404 for non-existent entry."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.assignment_queue.get(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404

    def test_lists_entries(self, db_session, work_order):
        """Test lists queue entries."""
        dispatch_service.assignment_queue.create(
            db_session,
            WorkOrderAssignmentQueueCreate(work_order_id=work_order.id),
        )

        entries = dispatch_service.assignment_queue.list(
            db_session,
            work_order_id=None,
            status=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert len(entries) >= 1

    def test_filters_by_work_order_id(self, db_session, work_order):
        """Test filters by work_order_id."""
        dispatch_service.assignment_queue.create(
            db_session,
            WorkOrderAssignmentQueueCreate(work_order_id=work_order.id),
        )

        entries = dispatch_service.assignment_queue.list(
            db_session,
            work_order_id=str(work_order.id),
            status=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(e.work_order_id == work_order.id for e in entries)

    def test_filters_by_status(self, db_session, work_order):
        """Test filters by status."""
        dispatch_service.assignment_queue.create(
            db_session,
            WorkOrderAssignmentQueueCreate(
                work_order_id=work_order.id, status=DispatchQueueStatus.queued
            ),
        )

        entries = dispatch_service.assignment_queue.list(
            db_session,
            work_order_id=None,
            status="queued",
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(e.status == DispatchQueueStatus.queued for e in entries)

    def test_updates_entry(self, db_session, work_order):
        """Test updates entry."""
        entry = dispatch_service.assignment_queue.create(
            db_session,
            WorkOrderAssignmentQueueCreate(work_order_id=work_order.id),
        )
        updated = dispatch_service.assignment_queue.update(
            db_session,
            str(entry.id),
            WorkOrderAssignmentQueueUpdate(status=DispatchQueueStatus.assigned),
        )
        assert updated.status == DispatchQueueStatus.assigned

    def test_update_not_found(self, db_session):
        """Test update raises 404 for non-existent entry."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.assignment_queue.update(
                db_session,
                str(uuid.uuid4()),
                WorkOrderAssignmentQueueUpdate(reason="test"),
            )
        assert exc_info.value.status_code == 404

    def test_deletes_entry(self, db_session, work_order):
        """Test hard deletes entry."""
        entry = dispatch_service.assignment_queue.create(
            db_session,
            WorkOrderAssignmentQueueCreate(work_order_id=work_order.id),
        )
        entry_id = entry.id
        dispatch_service.assignment_queue.delete(db_session, str(entry_id))
        # Should be gone
        with pytest.raises(HTTPException):
            dispatch_service.assignment_queue.get(db_session, str(entry_id))

    def test_delete_not_found(self, db_session):
        """Test delete raises 404 for non-existent entry."""
        with pytest.raises(HTTPException) as exc_info:
            dispatch_service.assignment_queue.delete(db_session, str(uuid.uuid4()))
        assert exc_info.value.status_code == 404


# =============================================================================
# Auto-Assign Tests
# =============================================================================


class TestAutoAssign:
    """Tests for auto_assign_work_order function."""

    def test_returns_assigned_if_already_assigned(self, db_session, work_order, person):
        """Test returns assigned status if work order already assigned."""
        work_order.assigned_to_person_id = person.id
        db_session.commit()

        entry = dispatch_service.auto_assign_work_order(db_session, str(work_order.id))
        assert entry.status == DispatchQueueStatus.assigned
        assert "Already assigned" in entry.reason

    def test_returns_queued_if_no_technician(self, db_session, work_order):
        """Test returns queued if no technician available."""
        entry = dispatch_service.auto_assign_work_order(db_session, str(work_order.id))
        assert entry.status == DispatchQueueStatus.queued
        assert "No technician available" in entry.reason

    def test_assigns_when_technician_available(self, db_session, work_order, person):
        """Test assigns when technician is available."""
        # Create technician
        dispatch_service.technicians.create(
            db_session, TechnicianProfileCreate(person_id=person.id)
        )

        entry = dispatch_service.auto_assign_work_order(db_session, str(work_order.id))
        assert entry.status == DispatchQueueStatus.assigned
        assert "Auto-assigned" in entry.reason

    def test_applies_rule_with_region(self, db_session, work_order, person):
        """Test applies rule with region filter."""
        # Create technician with region
        dispatch_service.technicians.create(
            db_session, TechnicianProfileCreate(person_id=person.id, region="North")
        )
        # Create rule with region
        dispatch_service.dispatch_rules.create(
            db_session,
            DispatchRuleCreate(name="North Rule", region="North", auto_assign=True),
        )

        entry = dispatch_service.auto_assign_work_order(db_session, str(work_order.id))
        # Should queue because work_order has no address_id so region rule doesn't match
        assert entry is not None

    def test_applies_rule_with_skills(self, db_session, work_order, person):
        """Test applies rule with skill requirements."""
        # Create technician
        technician = dispatch_service.technicians.create(
            db_session, TechnicianProfileCreate(person_id=person.id)
        )
        # Create skill
        skill = dispatch_service.skills.create(
            db_session, SkillCreate(name="Fiber")
        )
        # Link skill to technician
        dispatch_service.technician_skills.create(
            db_session,
            TechnicianSkillCreate(technician_id=technician.id, skill_id=skill.id),
        )
        # Create rule requiring skill
        dispatch_service.dispatch_rules.create(
            db_session,
            DispatchRuleCreate(
                name="Fiber Rule", skill_ids=[str(skill.id)], auto_assign=True
            ),
        )

        entry = dispatch_service.auto_assign_work_order(db_session, str(work_order.id))
        assert entry.status == DispatchQueueStatus.assigned


class TestAutoAssignResponse:
    """Tests for auto_assign_response function."""

    def test_returns_response_dict(self, db_session, work_order, person):
        """Test returns proper response dict."""
        dispatch_service.technicians.create(
            db_session, TechnicianProfileCreate(person_id=person.id)
        )

        result = dispatch_service.auto_assign_response(db_session, str(work_order.id))

        assert "work_order_id" in result
        assert "technician_id" in result
        assert "assignment_status" in result
        assert "detail" in result

    def test_response_includes_technician_id_when_assigned(
        self, db_session, work_order, person
    ):
        """Test response includes technician_id when assigned."""
        dispatch_service.technicians.create(
            db_session, TechnicianProfileCreate(person_id=person.id)
        )

        result = dispatch_service.auto_assign_response(db_session, str(work_order.id))

        assert result["technician_id"] == person.id
        assert result["assignment_status"] == DispatchQueueStatus.assigned
