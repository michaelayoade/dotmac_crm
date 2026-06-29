"""Customer installation tracker: project → portal payload (stages + progress)."""

from __future__ import annotations

from types import SimpleNamespace

from app.models.projects import ProjectStatus, ProjectType, TaskStatus
from app.services.projects import build_portal_project_payload


def _task(stage_key, status, title="t", completed_at=None):
    return SimpleNamespace(
        title=title,
        status=status,
        completed_at=completed_at,
        is_active=True,
        metadata_={"fiber_stage_key": stage_key} if stage_key else {},
    )


def _project(tasks, status=ProjectStatus.active, ptype=ProjectType.fiber_optics_installation):
    return SimpleNamespace(
        id="p1",
        name="Fiber install",
        status=status,
        project_type=ptype,
        customer_address="12 Test St",
        region="Abuja",
        start_at=None,
        due_at=None,
        completed_at=None,
        created_at=None,
        tasks=tasks,
    )


def test_fiber_payload_orders_stages_and_computes_progress():
    tasks = [
        _task("drop_cable_installation", TaskStatus.in_progress),
        _task("project_plan", TaskStatus.done),
        _task("project_survey", TaskStatus.done),
    ]
    out = build_portal_project_payload(_project(tasks))
    titles = [s["title"] for s in out["stages"]]
    # Canonical order, not insertion order.
    assert titles[0] == "Project Plan"
    assert titles[1] == "Project Survey"
    assert titles[2] == "Drop Cable Installation"
    # 6 canonical stages; 2 done → 33%.
    assert len(out["stages"]) == 6
    assert out["progress_pct"] == 33
    assert out["current_stage"] == "Drop Cable Installation"


def test_completed_project_is_100_pct_no_current_stage():
    tasks = [_task("project_plan", TaskStatus.done)]
    out = build_portal_project_payload(_project(tasks, status=ProjectStatus.completed))
    assert out["progress_pct"] == 100
    assert out["current_stage"] is None
    assert out["status"] == "completed"


def test_generic_project_uses_per_task_stages():
    tasks = [
        _task(None, TaskStatus.done, title="Survey"),
        _task(None, TaskStatus.todo, title="Cabling"),
    ]
    out = build_portal_project_payload(_project(tasks, ptype=ProjectType.cross_connect))
    assert [s["title"] for s in out["stages"]] == ["Survey", "Cabling"]
    assert out["progress_pct"] == 50
    assert out["current_stage"] == "Cabling"
