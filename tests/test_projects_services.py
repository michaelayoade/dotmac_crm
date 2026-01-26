"""Tests for projects service."""

import uuid

import pytest
from fastapi import HTTPException

from app.models.domain_settings import DomainSetting, SettingDomain
from app.models.projects import Project, ProjectPriority, ProjectStatus, ProjectTask, TaskPriority, TaskStatus
from app.models.subscription_engine import SettingValueType
from app.schemas.projects import ProjectCreate, ProjectTaskCreate, ProjectTaskUpdate, ProjectUpdate
from app.services import projects as projects_service
from app.services.common import apply_ordering, apply_pagination, validate_enum


# =============================================================================
# Helper Function Tests
# =============================================================================


class TestApplyOrdering:
    """Tests for _apply_ordering function."""

    def test_valid_order_by_asc(self, db_session):
        """Test valid order_by with asc direction."""
        query = db_session.query(Project)
        allowed = {"name": Project.name, "created_at": Project.created_at}
        result = apply_ordering(query, "name", "asc", allowed)
        assert result is not None

    def test_valid_order_by_desc(self, db_session):
        """Test valid order_by with desc direction."""
        query = db_session.query(Project)
        allowed = {"name": Project.name, "created_at": Project.created_at}
        result = apply_ordering(query, "name", "desc", allowed)
        assert result is not None

    def test_invalid_order_by(self, db_session):
        """Test invalid order_by raises HTTPException."""
        query = db_session.query(Project)
        allowed = {"name": Project.name}

        with pytest.raises(HTTPException) as exc_info:
            apply_ordering(query, "invalid_column", "asc", allowed)

        assert exc_info.value.status_code == 400
        assert "Invalid order_by" in exc_info.value.detail


class TestApplyPagination:
    """Tests for _apply_pagination function."""

    def test_applies_limit_and_offset(self, db_session):
        """Test applies limit and offset to query."""
        query = db_session.query(Project)
        result = apply_pagination(query, 10, 5)
        assert result is not None


class TestValidateEnum:
    """Tests for _validate_enum function."""

    def test_returns_none_for_none(self):
        """Test returns None for None input."""
        result = validate_enum(None, ProjectStatus, "status")
        assert result is None

    def test_converts_valid_string(self):
        """Test converts valid string to enum."""
        result = validate_enum("planned", ProjectStatus, "status")
        assert result == ProjectStatus.planned

    def test_invalid_string_raises(self):
        """Test invalid string raises HTTPException."""
        with pytest.raises(HTTPException) as exc_info:
            validate_enum("invalid_status", ProjectStatus, "status")

        assert exc_info.value.status_code == 400
        assert "Invalid status" in exc_info.value.detail


class TestEnsurePerson:
    """Tests for _ensure_person function."""

    def test_valid_person(self, db_session, person):
        """Test returns normally for valid person."""
        # Should not raise
        projects_service._ensure_person(db_session, str(person.id))

    def test_invalid_person_raises(self, db_session):
        """Test raises HTTPException for non-existent person."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service._ensure_person(db_session, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404
        assert "Person not found" in exc_info.value.detail


class TestEnsureAccount:
    """Tests for _ensure_account function."""

    def test_valid_account(self, db_session, subscriber_account):
        """Test returns normally for valid account."""
        # Should not raise
        projects_service._ensure_account(db_session, str(subscriber_account.id))

    def test_invalid_account_raises(self, db_session):
        """Test raises HTTPException for non-existent account."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service._ensure_account(db_session, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404
        assert "Subscriber account not found" in exc_info.value.detail


# =============================================================================
# Projects CRUD Tests
# =============================================================================


class TestProjectsCreate:
    """Tests for Projects.create."""

    def test_creates_project(self, db_session, subscriber_account):
        """Test creates a project."""
        project = projects_service.projects.create(
            db_session,
            ProjectCreate(
                name="Test Project",
                account_id=subscriber_account.id,
            ),
        )
        assert project.name == "Test Project"
        assert project.account_id == subscriber_account.id
        assert project.is_active is True

    def test_creates_project_minimal(self, db_session):
        """Test creates a project with minimal fields."""
        project = projects_service.projects.create(
            db_session,
            ProjectCreate(name="Minimal Project"),
        )
        assert project.name == "Minimal Project"
        assert project.account_id is None
        assert project.is_active is True

    def test_creates_project_with_owner(self, db_session, person):
        """Test creates a project with owner."""
        project = projects_service.projects.create(
            db_session,
            ProjectCreate(
                name="Owned Project",
                owner_person_id=person.id,
            ),
        )
        assert project.owner_person_id == person.id

    def test_creates_project_with_manager(self, db_session, person):
        """Test creates a project with manager."""
        project = projects_service.projects.create(
            db_session,
            ProjectCreate(
                name="Managed Project",
                manager_person_id=person.id,
            ),
        )
        assert project.manager_person_id == person.id

    def test_raises_for_invalid_account(self, db_session):
        """Test raises for non-existent account."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.projects.create(
                db_session,
                ProjectCreate(
                    name="Test",
                    account_id=uuid.uuid4(),
                ),
            )

        assert exc_info.value.status_code == 404
        assert "Subscriber account not found" in exc_info.value.detail

    def test_raises_for_invalid_owner(self, db_session):
        """Test raises for non-existent owner."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.projects.create(
                db_session,
                ProjectCreate(
                    name="Test",
                    owner_person_id=uuid.uuid4(),
                ),
            )

        assert exc_info.value.status_code == 404
        assert "Person not found" in exc_info.value.detail

    def test_raises_for_invalid_manager(self, db_session):
        """Test raises for non-existent manager."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.projects.create(
                db_session,
                ProjectCreate(
                    name="Test",
                    manager_person_id=uuid.uuid4(),
                ),
            )

        assert exc_info.value.status_code == 404
        assert "Person not found" in exc_info.value.detail

    def test_applies_default_status_from_settings(self, db_session):
        """Test applies default status from settings."""
        setting = DomainSetting(
            domain=SettingDomain.projects,
            key="default_project_status",
            value_type=SettingValueType.string,
            value_text="active",
            is_active=True,
        )
        db_session.add(setting)
        db_session.commit()

        project = projects_service.projects.create(
            db_session,
            ProjectCreate(name="Default Status Test"),
        )
        assert project.status == ProjectStatus.active

    def test_applies_default_priority_from_settings(self, db_session):
        """Test applies default priority from settings."""
        setting = DomainSetting(
            domain=SettingDomain.projects,
            key="default_project_priority",
            value_type=SettingValueType.string,
            value_text="high",
            is_active=True,
        )
        db_session.add(setting)
        db_session.commit()

        project = projects_service.projects.create(
            db_session,
            ProjectCreate(name="Default Priority Test"),
        )
        assert project.priority == ProjectPriority.high

    def test_explicit_status_overrides_default(self, db_session):
        """Test explicit status overrides default setting."""
        setting = DomainSetting(
            domain=SettingDomain.projects,
            key="default_project_status",
            value_type=SettingValueType.string,
            value_text="active",
            is_active=True,
        )
        db_session.add(setting)
        db_session.commit()

        project = projects_service.projects.create(
            db_session,
            ProjectCreate(name="Explicit Status", status=ProjectStatus.on_hold),
        )
        assert project.status == ProjectStatus.on_hold


class TestProjectsGet:
    """Tests for Projects.get."""

    def test_gets_project_by_id(self, db_session, project):
        """Test gets project by ID."""
        fetched = projects_service.projects.get(db_session, str(project.id))
        assert fetched.id == project.id
        assert fetched.name == project.name

    def test_raises_for_not_found(self, db_session):
        """Test raises 404 for non-existent project."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.projects.get(db_session, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404
        assert "Project not found" in exc_info.value.detail


class TestProjectsList:
    """Tests for Projects.list."""

    def test_lists_active_projects(self, db_session, subscriber_account):
        """Test lists active projects by default."""
        projects_service.projects.create(
            db_session,
            ProjectCreate(name="List Test 1", account_id=subscriber_account.id),
        )
        projects_service.projects.create(
            db_session,
            ProjectCreate(name="List Test 2", account_id=subscriber_account.id),
        )

        projects = projects_service.projects.list(
            db_session,
            account_id=None,
            status=None,
            priority=None,
            owner_person_id=None,
            manager_person_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert len(projects) >= 2
        assert all(p.is_active for p in projects)

    def test_filters_by_account_id(self, db_session, subscriber_account):
        """Test filters by account_id."""
        projects_service.projects.create(
            db_session,
            ProjectCreate(name="Account Filter", account_id=subscriber_account.id),
        )

        projects = projects_service.projects.list(
            db_session,
            account_id=str(subscriber_account.id),
            status=None,
            priority=None,
            owner_person_id=None,
            manager_person_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(p.account_id == subscriber_account.id for p in projects)

    def test_filters_by_status(self, db_session):
        """Test filters by status."""
        projects_service.projects.create(
            db_session,
            ProjectCreate(name="Status Filter", status=ProjectStatus.active),
        )

        projects = projects_service.projects.list(
            db_session,
            account_id=None,
            status="active",
            priority=None,
            owner_person_id=None,
            manager_person_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(p.status == ProjectStatus.active for p in projects)

    def test_filters_by_priority(self, db_session):
        """Test filters by priority."""
        projects_service.projects.create(
            db_session,
            ProjectCreate(name="Priority Filter", priority=ProjectPriority.high),
        )

        projects = projects_service.projects.list(
            db_session,
            account_id=None,
            status=None,
            priority="high",
            owner_person_id=None,
            manager_person_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(p.priority == ProjectPriority.high for p in projects)

    def test_filters_by_owner_person_id(self, db_session, person):
        """Test filters by owner_person_id."""
        projects_service.projects.create(
            db_session,
            ProjectCreate(name="Owner Filter", owner_person_id=person.id),
        )

        projects = projects_service.projects.list(
            db_session,
            account_id=None,
            status=None,
            priority=None,
            owner_person_id=str(person.id),
            manager_person_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(str(p.owner_person_id) == str(person.id) for p in projects)

    def test_filters_by_manager_person_id(self, db_session, person):
        """Test filters by manager_person_id."""
        projects_service.projects.create(
            db_session,
            ProjectCreate(name="Manager Filter", manager_person_id=person.id),
        )

        projects = projects_service.projects.list(
            db_session,
            account_id=None,
            status=None,
            priority=None,
            owner_person_id=None,
            manager_person_id=str(person.id),
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(str(p.manager_person_id) == str(person.id) for p in projects)

    def test_filters_by_is_active_false(self, db_session):
        """Test filters by is_active=False."""
        proj = projects_service.projects.create(
            db_session,
            ProjectCreate(name="Inactive Filter"),
        )
        projects_service.projects.delete(db_session, str(proj.id))

        projects = projects_service.projects.list(
            db_session,
            account_id=None,
            status=None,
            priority=None,
            owner_person_id=None,
            manager_person_id=None,
            is_active=False,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(not p.is_active for p in projects)

    def test_order_by_name(self, db_session):
        """Test order by name."""
        projects_service.projects.create(
            db_session,
            ProjectCreate(name="ZZZ Project"),
        )
        projects_service.projects.create(
            db_session,
            ProjectCreate(name="AAA Project"),
        )

        projects = projects_service.projects.list(
            db_session,
            account_id=None,
            status=None,
            priority=None,
            owner_person_id=None,
            manager_person_id=None,
            is_active=None,
            order_by="name",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        names = [p.name for p in projects]
        assert names == sorted(names)

    def test_order_by_priority(self, db_session):
        """Test order by priority."""
        projects = projects_service.projects.list(
            db_session,
            account_id=None,
            status=None,
            priority=None,
            owner_person_id=None,
            manager_person_id=None,
            is_active=None,
            order_by="priority",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert projects is not None

    def test_invalid_status_filter_raises(self, db_session):
        """Test invalid status filter raises."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.projects.list(
                db_session,
                account_id=None,
                status="invalid_status",
                priority=None,
                owner_person_id=None,
                manager_person_id=None,
                is_active=None,
                order_by="created_at",
                order_dir="asc",
                limit=100,
                offset=0,
            )

        assert exc_info.value.status_code == 400
        assert "Invalid status" in exc_info.value.detail


class TestProjectsUpdate:
    """Tests for Projects.update."""

    def test_updates_project(self, db_session, project):
        """Test updates project fields."""
        updated = projects_service.projects.update(
            db_session,
            str(project.id),
            ProjectUpdate(name="Updated Name"),
        )
        assert updated.name == "Updated Name"

    def test_updates_status(self, db_session, project):
        """Test updates status."""
        updated = projects_service.projects.update(
            db_session,
            str(project.id),
            ProjectUpdate(status=ProjectStatus.completed),
        )
        assert updated.status == ProjectStatus.completed

    def test_updates_account_id(self, db_session, project, subscriber_account):
        """Test updates account_id."""
        updated = projects_service.projects.update(
            db_session,
            str(project.id),
            ProjectUpdate(account_id=subscriber_account.id),
        )
        assert updated.account_id == subscriber_account.id

    def test_updates_owner_person_id(self, db_session, project, person):
        """Test updates owner_person_id."""
        updated = projects_service.projects.update(
            db_session,
            str(project.id),
            ProjectUpdate(owner_person_id=person.id),
        )
        assert updated.owner_person_id == person.id

    def test_updates_manager_person_id(self, db_session, project, person):
        """Test updates manager_person_id."""
        updated = projects_service.projects.update(
            db_session,
            str(project.id),
            ProjectUpdate(manager_person_id=person.id),
        )
        assert updated.manager_person_id == person.id

    def test_raises_for_not_found(self, db_session):
        """Test raises 404 for non-existent project."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.projects.update(
                db_session, str(uuid.uuid4()), ProjectUpdate(name="new")
            )

        assert exc_info.value.status_code == 404

    def test_raises_for_invalid_account_on_update(self, db_session, project):
        """Test raises for invalid account on update."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.projects.update(
                db_session,
                str(project.id),
                ProjectUpdate(account_id=uuid.uuid4()),
            )

        assert exc_info.value.status_code == 404
        assert "Subscriber account not found" in exc_info.value.detail

    def test_raises_for_invalid_owner_on_update(self, db_session, project):
        """Test raises for invalid owner on update."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.projects.update(
                db_session,
                str(project.id),
                ProjectUpdate(owner_person_id=uuid.uuid4()),
            )

        assert exc_info.value.status_code == 404
        assert "Person not found" in exc_info.value.detail

    def test_raises_for_invalid_manager_on_update(self, db_session, project):
        """Test raises for invalid manager on update."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.projects.update(
                db_session,
                str(project.id),
                ProjectUpdate(manager_person_id=uuid.uuid4()),
            )

        assert exc_info.value.status_code == 404
        assert "Person not found" in exc_info.value.detail


class TestProjectsDelete:
    """Tests for Projects.delete."""

    def test_soft_deletes_project(self, db_session):
        """Test soft deletes project."""
        proj = projects_service.projects.create(
            db_session,
            ProjectCreate(name="Delete Test"),
        )
        project_id = str(proj.id)
        projects_service.projects.delete(db_session, project_id)
        db_session.refresh(proj)
        assert proj.is_active is False

    def test_raises_for_not_found(self, db_session):
        """Test raises 404 for non-existent project."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.projects.delete(db_session, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404


# =============================================================================
# ProjectTasks CRUD Tests
# =============================================================================


class TestProjectTasksCreate:
    """Tests for ProjectTasks.create."""

    def test_creates_task(self, db_session, project):
        """Test creates a project task."""
        task = projects_service.project_tasks.create(
            db_session,
            ProjectTaskCreate(
                project_id=project.id,
                title="Test Task",
            ),
        )
        assert task.title == "Test Task"
        assert task.project_id == project.id
        assert task.is_active is True

    def test_creates_task_with_parent(self, db_session, project):
        """Test creates a task with parent task."""
        parent = projects_service.project_tasks.create(
            db_session,
            ProjectTaskCreate(project_id=project.id, title="Parent Task"),
        )
        child = projects_service.project_tasks.create(
            db_session,
            ProjectTaskCreate(
                project_id=project.id,
                title="Child Task",
                parent_task_id=parent.id,
            ),
        )
        assert child.parent_task_id == parent.id

    def test_creates_task_with_assignee(self, db_session, project, person):
        """Test creates a task with assignee."""
        task = projects_service.project_tasks.create(
            db_session,
            ProjectTaskCreate(
                project_id=project.id,
                title="Assigned Task",
                assigned_to_person_id=person.id,
            ),
        )
        assert task.assigned_to_person_id == person.id

    def test_creates_task_with_creator(self, db_session, project, person):
        """Test creates a task with creator."""
        task = projects_service.project_tasks.create(
            db_session,
            ProjectTaskCreate(
                project_id=project.id,
                title="Created Task",
                created_by_person_id=person.id,
            ),
        )
        assert task.created_by_person_id == person.id

    def test_creates_task_with_ticket(self, db_session, project, ticket):
        """Test creates a task linked to ticket."""
        task = projects_service.project_tasks.create(
            db_session,
            ProjectTaskCreate(
                project_id=project.id,
                title="Ticket Task",
                ticket_id=ticket.id,
            ),
        )
        assert task.ticket_id == ticket.id

    def test_creates_task_with_work_order(self, db_session, project, work_order):
        """Test creates a task linked to work order."""
        task = projects_service.project_tasks.create(
            db_session,
            ProjectTaskCreate(
                project_id=project.id,
                title="Work Order Task",
                work_order_id=work_order.id,
            ),
        )
        assert task.work_order_id == work_order.id

    def test_raises_for_invalid_project(self, db_session):
        """Test raises for non-existent project."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.project_tasks.create(
                db_session,
                ProjectTaskCreate(
                    project_id=uuid.uuid4(),
                    title="Test",
                ),
            )

        assert exc_info.value.status_code == 404
        assert "Project not found" in exc_info.value.detail

    def test_raises_for_invalid_parent_task(self, db_session, project):
        """Test raises for non-existent parent task."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.project_tasks.create(
                db_session,
                ProjectTaskCreate(
                    project_id=project.id,
                    title="Test",
                    parent_task_id=uuid.uuid4(),
                ),
            )

        assert exc_info.value.status_code == 404
        assert "Parent task not found" in exc_info.value.detail

    def test_raises_for_invalid_assignee(self, db_session, project):
        """Test raises for non-existent assignee."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.project_tasks.create(
                db_session,
                ProjectTaskCreate(
                    project_id=project.id,
                    title="Test",
                    assigned_to_person_id=uuid.uuid4(),
                ),
            )

        assert exc_info.value.status_code == 404
        assert "Person not found" in exc_info.value.detail

    def test_raises_for_invalid_creator(self, db_session, project):
        """Test raises for non-existent creator."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.project_tasks.create(
                db_session,
                ProjectTaskCreate(
                    project_id=project.id,
                    title="Test",
                    created_by_person_id=uuid.uuid4(),
                ),
            )

        assert exc_info.value.status_code == 404
        assert "Person not found" in exc_info.value.detail

    def test_raises_for_invalid_ticket(self, db_session, project):
        """Test raises for non-existent ticket."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.project_tasks.create(
                db_session,
                ProjectTaskCreate(
                    project_id=project.id,
                    title="Test",
                    ticket_id=uuid.uuid4(),
                ),
            )

        assert exc_info.value.status_code == 404
        assert "Ticket not found" in exc_info.value.detail

    def test_raises_for_invalid_work_order(self, db_session, project):
        """Test raises for non-existent work order."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.project_tasks.create(
                db_session,
                ProjectTaskCreate(
                    project_id=project.id,
                    title="Test",
                    work_order_id=uuid.uuid4(),
                ),
            )

        assert exc_info.value.status_code == 404
        assert "Work order not found" in exc_info.value.detail

    def test_applies_default_task_status_from_settings(self, db_session, project):
        """Test applies default task status from settings."""
        setting = DomainSetting(
            domain=SettingDomain.projects,
            key="default_task_status",
            value_type=SettingValueType.string,
            value_text="in_progress",
            is_active=True,
        )
        db_session.add(setting)
        db_session.commit()

        task = projects_service.project_tasks.create(
            db_session,
            ProjectTaskCreate(project_id=project.id, title="Default Status"),
        )
        assert task.status == TaskStatus.in_progress

    def test_applies_default_task_priority_from_settings(self, db_session, project):
        """Test applies default task priority from settings."""
        setting = DomainSetting(
            domain=SettingDomain.projects,
            key="default_task_priority",
            value_type=SettingValueType.string,
            value_text="high",
            is_active=True,
        )
        db_session.add(setting)
        db_session.commit()

        task = projects_service.project_tasks.create(
            db_session,
            ProjectTaskCreate(project_id=project.id, title="Default Priority"),
        )
        assert task.priority == TaskPriority.high


class TestProjectTasksGet:
    """Tests for ProjectTasks.get."""

    def test_gets_task_by_id(self, db_session, project_task):
        """Test gets task by ID."""
        fetched = projects_service.project_tasks.get(db_session, str(project_task.id))
        assert fetched.id == project_task.id
        assert fetched.title == project_task.title

    def test_raises_for_not_found(self, db_session):
        """Test raises 404 for non-existent task."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.project_tasks.get(db_session, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404
        assert "Project task not found" in exc_info.value.detail


class TestProjectTasksList:
    """Tests for ProjectTasks.list."""

    def test_lists_active_tasks(self, db_session, project):
        """Test lists active tasks by default."""
        projects_service.project_tasks.create(
            db_session,
            ProjectTaskCreate(project_id=project.id, title="List Test 1"),
        )
        projects_service.project_tasks.create(
            db_session,
            ProjectTaskCreate(project_id=project.id, title="List Test 2"),
        )

        tasks = projects_service.project_tasks.list(
            db_session,
            project_id=None,
            status=None,
            priority=None,
            assigned_to_person_id=None,
            parent_task_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert len(tasks) >= 2
        assert all(t.is_active for t in tasks)

    def test_filters_by_project_id(self, db_session, project):
        """Test filters by project_id."""
        projects_service.project_tasks.create(
            db_session,
            ProjectTaskCreate(project_id=project.id, title="Project Filter"),
        )

        tasks = projects_service.project_tasks.list(
            db_session,
            project_id=str(project.id),
            status=None,
            priority=None,
            assigned_to_person_id=None,
            parent_task_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(t.project_id == project.id for t in tasks)

    def test_filters_by_status(self, db_session, project):
        """Test filters by status."""
        projects_service.project_tasks.create(
            db_session,
            ProjectTaskCreate(
                project_id=project.id,
                title="Status Filter",
                status=TaskStatus.in_progress,
            ),
        )

        tasks = projects_service.project_tasks.list(
            db_session,
            project_id=None,
            status="in_progress",
            priority=None,
            assigned_to_person_id=None,
            parent_task_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(t.status == TaskStatus.in_progress for t in tasks)

    def test_filters_by_priority(self, db_session, project):
        """Test filters by priority."""
        projects_service.project_tasks.create(
            db_session,
            ProjectTaskCreate(
                project_id=project.id,
                title="Priority Filter",
                priority=TaskPriority.urgent,
            ),
        )

        tasks = projects_service.project_tasks.list(
            db_session,
            project_id=None,
            status=None,
            priority="urgent",
            assigned_to_person_id=None,
            parent_task_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(t.priority == TaskPriority.urgent for t in tasks)

    def test_filters_by_assigned_to_person_id(self, db_session, project, person):
        """Test filters by assigned_to_person_id."""
        projects_service.project_tasks.create(
            db_session,
            ProjectTaskCreate(
                project_id=project.id,
                title="Assignee Filter",
                assigned_to_person_id=person.id,
            ),
        )

        tasks = projects_service.project_tasks.list(
            db_session,
            project_id=None,
            status=None,
            priority=None,
            assigned_to_person_id=str(person.id),
            parent_task_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(str(t.assigned_to_person_id) == str(person.id) for t in tasks)

    def test_filters_by_parent_task_id(self, db_session, project):
        """Test filters by parent_task_id."""
        parent = projects_service.project_tasks.create(
            db_session,
            ProjectTaskCreate(project_id=project.id, title="Parent"),
        )
        projects_service.project_tasks.create(
            db_session,
            ProjectTaskCreate(
                project_id=project.id,
                title="Child",
                parent_task_id=parent.id,
            ),
        )

        tasks = projects_service.project_tasks.list(
            db_session,
            project_id=None,
            status=None,
            priority=None,
            assigned_to_person_id=None,
            parent_task_id=str(parent.id),
            is_active=None,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(t.parent_task_id == parent.id for t in tasks)

    def test_filters_by_is_active_false(self, db_session, project):
        """Test filters by is_active=False."""
        task = projects_service.project_tasks.create(
            db_session,
            ProjectTaskCreate(project_id=project.id, title="Inactive Filter"),
        )
        projects_service.project_tasks.delete(db_session, str(task.id))

        tasks = projects_service.project_tasks.list(
            db_session,
            project_id=None,
            status=None,
            priority=None,
            assigned_to_person_id=None,
            parent_task_id=None,
            is_active=False,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(not t.is_active for t in tasks)

    def test_order_by_status(self, db_session, project):
        """Test order by status."""
        tasks = projects_service.project_tasks.list(
            db_session,
            project_id=str(project.id),
            status=None,
            priority=None,
            assigned_to_person_id=None,
            parent_task_id=None,
            is_active=None,
            order_by="status",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert tasks is not None

    def test_order_by_priority(self, db_session, project):
        """Test order by priority."""
        tasks = projects_service.project_tasks.list(
            db_session,
            project_id=str(project.id),
            status=None,
            priority=None,
            assigned_to_person_id=None,
            parent_task_id=None,
            is_active=None,
            order_by="priority",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        assert tasks is not None

    def test_invalid_status_filter_raises(self, db_session):
        """Test invalid status filter raises."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.project_tasks.list(
                db_session,
                project_id=None,
                status="invalid_status",
                priority=None,
                assigned_to_person_id=None,
                parent_task_id=None,
                is_active=None,
                order_by="created_at",
                order_dir="asc",
                limit=100,
                offset=0,
            )

        assert exc_info.value.status_code == 400
        assert "Invalid status" in exc_info.value.detail


class TestProjectTasksUpdate:
    """Tests for ProjectTasks.update."""

    def test_updates_task(self, db_session, project_task):
        """Test updates task fields."""
        updated = projects_service.project_tasks.update(
            db_session,
            str(project_task.id),
            ProjectTaskUpdate(title="Updated Title"),
        )
        assert updated.title == "Updated Title"

    def test_updates_status(self, db_session, project_task):
        """Test updates status."""
        updated = projects_service.project_tasks.update(
            db_session,
            str(project_task.id),
            ProjectTaskUpdate(status=TaskStatus.done),
        )
        assert updated.status == TaskStatus.done

    def test_updates_project_id(self, db_session, project_task):
        """Test updates project_id."""
        new_project = projects_service.projects.create(
            db_session,
            ProjectCreate(name="New Project"),
        )
        updated = projects_service.project_tasks.update(
            db_session,
            str(project_task.id),
            ProjectTaskUpdate(project_id=new_project.id),
        )
        assert updated.project_id == new_project.id

    def test_updates_parent_task_id(self, db_session, project, project_task):
        """Test updates parent_task_id."""
        new_parent = projects_service.project_tasks.create(
            db_session,
            ProjectTaskCreate(project_id=project.id, title="New Parent"),
        )
        updated = projects_service.project_tasks.update(
            db_session,
            str(project_task.id),
            ProjectTaskUpdate(parent_task_id=new_parent.id),
        )
        assert updated.parent_task_id == new_parent.id

    def test_updates_assigned_to_person_id(self, db_session, project_task, person):
        """Test updates assigned_to_person_id."""
        updated = projects_service.project_tasks.update(
            db_session,
            str(project_task.id),
            ProjectTaskUpdate(assigned_to_person_id=person.id),
        )
        assert updated.assigned_to_person_id == person.id

    def test_updates_created_by_person_id(self, db_session, project_task, person):
        """Test updates created_by_person_id."""
        updated = projects_service.project_tasks.update(
            db_session,
            str(project_task.id),
            ProjectTaskUpdate(created_by_person_id=person.id),
        )
        assert updated.created_by_person_id == person.id

    def test_updates_ticket_id(self, db_session, project_task, ticket):
        """Test updates ticket_id."""
        updated = projects_service.project_tasks.update(
            db_session,
            str(project_task.id),
            ProjectTaskUpdate(ticket_id=ticket.id),
        )
        assert updated.ticket_id == ticket.id

    def test_updates_work_order_id(self, db_session, project_task, work_order):
        """Test updates work_order_id."""
        updated = projects_service.project_tasks.update(
            db_session,
            str(project_task.id),
            ProjectTaskUpdate(work_order_id=work_order.id),
        )
        assert updated.work_order_id == work_order.id

    def test_raises_for_not_found(self, db_session):
        """Test raises 404 for non-existent task."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.project_tasks.update(
                db_session, str(uuid.uuid4()), ProjectTaskUpdate(title="new")
            )

        assert exc_info.value.status_code == 404

    def test_raises_for_invalid_project_on_update(self, db_session, project_task):
        """Test raises for invalid project on update."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.project_tasks.update(
                db_session,
                str(project_task.id),
                ProjectTaskUpdate(project_id=uuid.uuid4()),
            )

        assert exc_info.value.status_code == 404
        assert "Project not found" in exc_info.value.detail

    def test_raises_for_invalid_parent_on_update(self, db_session, project_task):
        """Test raises for invalid parent on update."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.project_tasks.update(
                db_session,
                str(project_task.id),
                ProjectTaskUpdate(parent_task_id=uuid.uuid4()),
            )

        assert exc_info.value.status_code == 404
        assert "Parent task not found" in exc_info.value.detail

    def test_raises_for_invalid_assignee_on_update(self, db_session, project_task):
        """Test raises for invalid assignee on update."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.project_tasks.update(
                db_session,
                str(project_task.id),
                ProjectTaskUpdate(assigned_to_person_id=uuid.uuid4()),
            )

        assert exc_info.value.status_code == 404
        assert "Person not found" in exc_info.value.detail

    def test_raises_for_invalid_creator_on_update(self, db_session, project_task):
        """Test raises for invalid creator on update."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.project_tasks.update(
                db_session,
                str(project_task.id),
                ProjectTaskUpdate(created_by_person_id=uuid.uuid4()),
            )

        assert exc_info.value.status_code == 404
        assert "Person not found" in exc_info.value.detail

    def test_raises_for_invalid_ticket_on_update(self, db_session, project_task):
        """Test raises for invalid ticket on update."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.project_tasks.update(
                db_session,
                str(project_task.id),
                ProjectTaskUpdate(ticket_id=uuid.uuid4()),
            )

        assert exc_info.value.status_code == 404
        assert "Ticket not found" in exc_info.value.detail

    def test_raises_for_invalid_work_order_on_update(self, db_session, project_task):
        """Test raises for invalid work order on update."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.project_tasks.update(
                db_session,
                str(project_task.id),
                ProjectTaskUpdate(work_order_id=uuid.uuid4()),
            )

        assert exc_info.value.status_code == 404
        assert "Work order not found" in exc_info.value.detail


class TestProjectTasksDelete:
    """Tests for ProjectTasks.delete."""

    def test_soft_deletes_task(self, db_session, project):
        """Test soft deletes task."""
        task = projects_service.project_tasks.create(
            db_session,
            ProjectTaskCreate(project_id=project.id, title="Delete Test"),
        )
        task_id = str(task.id)
        projects_service.project_tasks.delete(db_session, task_id)
        db_session.refresh(task)
        assert task.is_active is False

    def test_raises_for_not_found(self, db_session):
        """Test raises 404 for non-existent task."""
        with pytest.raises(HTTPException) as exc_info:
            projects_service.project_tasks.delete(db_session, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404
