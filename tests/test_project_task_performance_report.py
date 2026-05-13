from datetime import UTC, datetime, timedelta

from app.models.person import Person
from app.models.projects import Project, ProjectTask, ProjectTaskAssignee, ProjectType, TaskStatus
from app.web.admin.reports import _get_project_task_people_performance


def _person(email: str, first_name: str, last_name: str) -> Person:
    return Person(first_name=first_name, last_name=last_name, display_name=f"{first_name} {last_name}", email=email)


def test_project_task_people_performance_counts_multi_assignee_tasks(db_session):
    start_at = datetime(2026, 5, 1, tzinfo=UTC)
    end_at = datetime(2026, 5, 7, 23, 59, tzinfo=UTC)
    alice = _person("alice.project@example.com", "Alice", "Project")
    bob = _person("bob.project@example.com", "Bob", "Project")
    project = Project(name="Fiber Build", project_type=ProjectType.fiber_optics_installation)
    db_session.add_all([alice, bob, project])
    db_session.flush()

    shared_task = ProjectTask(
        project_id=project.id,
        title="Install fiber drop",
        status=TaskStatus.done,
        start_at=start_at + timedelta(hours=1),
        due_at=start_at + timedelta(hours=8),
        completed_at=start_at + timedelta(hours=5),
        effort_hours=4,
        created_at=start_at + timedelta(minutes=10),
    )
    blocked_task = ProjectTask(
        project_id=project.id,
        title="Activate ONT",
        status=TaskStatus.blocked,
        assigned_to_person_id=alice.id,
        due_at=start_at + timedelta(hours=6),
        created_at=start_at + timedelta(minutes=20),
    )
    db_session.add_all([shared_task, blocked_task])
    db_session.flush()
    db_session.add_all(
        [
            ProjectTaskAssignee(task_id=shared_task.id, person_id=alice.id),
            ProjectTaskAssignee(task_id=shared_task.id, person_id=bob.id),
        ]
    )
    db_session.commit()

    rows, summary, project_type_breakdown, recent_completions = _get_project_task_people_performance(
        db_session, start_at, end_at
    )

    by_name = {row["name"]: row for row in rows}
    assert by_name["Alice Project"]["assigned_tasks"] == 2
    assert by_name["Alice Project"]["completed_tasks"] == 1
    assert by_name["Alice Project"]["blocked_tasks"] == 1
    assert by_name["Alice Project"]["overdue_tasks"] == 1
    assert by_name["Alice Project"]["completion_rate"] == 50.0
    assert by_name["Bob Project"]["assigned_tasks"] == 1
    assert by_name["Bob Project"]["completed_tasks"] == 1
    assert by_name["Bob Project"]["performance_score"] == 100.0
    assert rows[0]["name"] == "Bob Project"
    assert summary["people_count"] == 2
    assert summary["tasks_assigned"] == 3
    assert summary["tasks_completed"] == 2
    assert project_type_breakdown["fiber_optics_installation"] == 2
    assert [task.title for task in recent_completions] == ["Install fiber drop"]


def test_project_task_people_performance_excludes_tasks_outside_window(db_session):
    start_at = datetime(2026, 5, 1, tzinfo=UTC)
    end_at = datetime(2026, 5, 7, 23, 59, tzinfo=UTC)
    person = _person("outside.project@example.com", "Outside", "Window")
    project = Project(name="Outside Project")
    db_session.add_all([person, project])
    db_session.flush()
    db_session.add(
        ProjectTask(
            project_id=project.id,
            title="Old task",
            status=TaskStatus.done,
            assigned_to_person_id=person.id,
            completed_at=start_at - timedelta(days=1),
            created_at=start_at - timedelta(days=2),
        )
    )
    db_session.commit()

    rows, summary, _, recent_completions = _get_project_task_people_performance(db_session, start_at, end_at)

    assert rows == []
    assert summary["tasks_assigned"] == 0
    assert recent_completions == []


def test_project_task_people_performance_includes_tasks_completed_within_window(db_session):
    start_at = datetime(2026, 5, 1, tzinfo=UTC)
    end_at = datetime(2026, 5, 7, 23, 59, tzinfo=UTC)
    person = _person("carryover.project@example.com", "Carryover", "Project")
    project = Project(name="Carryover Project")
    db_session.add_all([person, project])
    db_session.flush()
    db_session.add(
        ProjectTask(
            project_id=project.id,
            title="Carryover completion",
            status=TaskStatus.done,
            assigned_to_person_id=person.id,
            created_at=start_at - timedelta(days=10),
            start_at=start_at - timedelta(days=9),
            due_at=start_at + timedelta(days=1),
            completed_at=start_at + timedelta(days=2),
            effort_hours=24,
        )
    )
    db_session.commit()

    rows, summary, _, recent_completions = _get_project_task_people_performance(db_session, start_at, end_at)

    assert len(rows) == 1
    assert rows[0]["name"] == "Carryover Project"
    assert rows[0]["assigned_tasks"] == 1
    assert rows[0]["completed_tasks"] == 1
    assert summary["tasks_assigned"] == 1
    assert summary["tasks_completed"] == 1
    assert [task.title for task in recent_completions] == ["Carryover completion"]
