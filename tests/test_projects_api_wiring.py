from app.api import projects as projects_api


def test_list_projects_wires_arguments_in_expected_order(monkeypatch, db_session):
    captured: dict[str, object] = {}

    def _fake_list_response(db, *args):
        captured["db"] = db
        captured["args"] = args
        return {"items": [], "count": 0, "limit": 50, "offset": 10}

    monkeypatch.setattr(projects_api.projects_service.projects, "list_response", _fake_list_response)

    response = projects_api.list_projects(
        subscriber_id="sub-1",
        status="active",
        project_type="fiber_optics_installation",
        priority="high",
        owner_person_id="owner-1",
        manager_person_id="manager-1",
        project_manager_person_id="pm-1",
        assistant_manager_person_id="spc-1",
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=50,
        offset=10,
        db=db_session,
    )

    assert response["count"] == 0
    assert captured["db"] is db_session
    assert captured["args"] == (
        "sub-1",
        "active",
        "fiber_optics_installation",
        "high",
        "owner-1",
        "manager-1",
        "pm-1",
        "spc-1",
        True,
        "created_at",
        "desc",
        50,
        10,
    )


def test_list_project_tasks_wires_arguments_in_expected_order(monkeypatch, db_session):
    captured: dict[str, object] = {}

    def _fake_list_response(db, *args):
        captured["db"] = db
        captured["args"] = args
        return {"items": [], "count": 0, "limit": 25, "offset": 5}

    monkeypatch.setattr(projects_api.projects_service.project_tasks, "list_response", _fake_list_response)

    response = projects_api.list_project_tasks(
        project_id="proj-1",
        status="in_progress",
        priority="urgent",
        assigned_to_person_id="tech-1",
        parent_task_id="parent-1",
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=25,
        offset=5,
        db=db_session,
    )

    assert response["count"] == 0
    assert captured["db"] is db_session
    assert captured["args"] == (
        "proj-1",
        "in_progress",
        "urgent",
        "tech-1",
        "parent-1",
        True,
        "created_at",
        "desc",
        25,
        5,
    )
