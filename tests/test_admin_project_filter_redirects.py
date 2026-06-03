import csv
from datetime import UTC, datetime
from io import StringIO
from urllib.parse import urlsplit

from starlette.requests import Request

from app.schemas.projects import ProjectCreate
from app.services import filter_preferences as preferences
from app.services import projects as projects_service
from app.web.admin import projects as admin_projects


def _make_request(path: str) -> Request:
    parsed = urlsplit(path)
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": parsed.path,
            "headers": [],
            "query_string": parsed.query.encode(),
        }
    )


def _stub_auth_helpers(monkeypatch, person):
    monkeypatch.setattr("app.web.admin._auth_helpers.get_sidebar_stats", lambda _db: {})
    monkeypatch.setattr(
        "app.web.admin._auth_helpers.get_current_user",
        lambda _request: {"person_id": str(person.id), "roles": [], "permissions": []},
    )


def test_projects_list_redirects_default_sort_and_page_size_query(monkeypatch, db_session, person):
    _stub_auth_helpers(monkeypatch, person)

    response = admin_projects.projects_list(
        request=_make_request("/admin/projects?order_by=created_at&order_dir=desc&per_page=25"),
        clear_filters=False,
        page=1,
        per_page=25,
        db=db_session,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/admin/projects"


def test_projects_list_strips_defaults_but_keeps_real_filters(monkeypatch, db_session, person):
    _stub_auth_helpers(monkeypatch, person)

    response = admin_projects.projects_list(
        request=_make_request("/admin/projects?status=active&order_by=created_at&order_dir=desc&per_page=25"),
        status="active",
        clear_filters=False,
        page=1,
        per_page=25,
        db=db_session,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/admin/projects?status=active"


def test_projects_list_keeps_non_default_sort_and_page_size(monkeypatch, db_session, person):
    _stub_auth_helpers(monkeypatch, person)
    monkeypatch.setattr(
        admin_projects.projects_service.projects,
        "list",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(admin_projects, "_load_project_region_options", lambda _db: [])

    response = admin_projects.projects_list(
        request=_make_request("/admin/projects?order_by=name&order_dir=asc&per_page=50"),
        order_by="name",
        order_dir="asc",
        clear_filters=False,
        page=1,
        per_page=50,
        db=db_session,
    )

    assert response.status_code == 200


def test_projects_list_filters_by_created_date_range(monkeypatch, db_session, person):
    older_project = projects_service.projects.create(
        db_session,
        ProjectCreate(name="Older project", status="active"),
    )
    matching_project = projects_service.projects.create(
        db_session,
        ProjectCreate(name="Matching project", status="active"),
    )
    older_project.created_at = datetime(2026, 4, 10, 12, 0, tzinfo=UTC)
    matching_project.created_at = datetime(2026, 4, 20, 12, 0, tzinfo=UTC)
    db_session.commit()
    _stub_auth_helpers(monkeypatch, person)

    response = admin_projects.projects_list(
        request=_make_request("/admin/projects?date_from=2026-04-15&date_to=2026-04-25"),
        date_from="2026-04-15",
        date_to="2026-04-25",
        clear_filters=False,
        page=1,
        per_page=25,
        db=db_session,
    )

    assert response.context["date_from"] == "2026-04-15"
    assert response.context["date_to"] == "2026-04-25"
    assert [project.id for project in response.context["projects"]] == [matching_project.id]
    body = response.body.decode()
    assert 'name="date_from"' in body
    assert 'name="date_to"' in body


def test_projects_list_rejects_invalid_date_range(monkeypatch, db_session, person):
    _stub_auth_helpers(monkeypatch, person)

    try:
        admin_projects.projects_list(
            request=_make_request("/admin/projects?date_from=2026-04-25&date_to=2026-04-15"),
            date_from="2026-04-25",
            date_to="2026-04-15",
            clear_filters=False,
            page=1,
            per_page=25,
            db=db_session,
        )
        raise AssertionError("Expected projects_list to reject invalid date range")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
        assert "From date" in str(getattr(exc, "detail", ""))


def test_projects_list_saves_date_filters_in_preferences(monkeypatch, db_session, person):
    project = projects_service.projects.create(
        db_session,
        ProjectCreate(name="Visible project", status="active"),
    )
    project.created_at = datetime(2026, 4, 20, 12, 0, tzinfo=UTC)
    db_session.commit()
    _stub_auth_helpers(monkeypatch, person)

    admin_projects.projects_list(
        request=_make_request("/admin/projects?date_from=2026-04-01&date_to=2026-04-30"),
        date_from="2026-04-01",
        date_to="2026-04-30",
        clear_filters=False,
        page=1,
        per_page=25,
        db=db_session,
    )

    assert preferences.get_preference(db_session, person.id, preferences.PROJECTS_PAGE.key) == {
        "date_from": "2026-04-01",
        "date_to": "2026-04-30",
    }


def test_projects_export_csv_respects_filters_and_columns(monkeypatch, db_session, person):
    older_project = projects_service.projects.create(
        db_session,
        ProjectCreate(name="Older export project", status="active", region="Wuse"),
    )
    matching_project = projects_service.projects.create(
        db_session,
        ProjectCreate(name="Matching export project", status="active", region="Garki"),
    )
    older_project.created_at = datetime(2026, 4, 10, 12, 0, tzinfo=UTC)
    matching_project.created_at = datetime(2026, 4, 20, 12, 0, tzinfo=UTC)
    db_session.commit()
    _stub_auth_helpers(monkeypatch, person)

    response = admin_projects.projects_export_csv(
        request=_make_request("/admin/projects/export.csv?region=Garki&date_from=2026-04-15&date_to=2026-04-25"),
        region="Garki",
        date_from="2026-04-15",
        date_to="2026-04-25",
        columns="project,region",
        order_by="created_at",
        order_dir="desc",
        db=db_session,
    )

    assert response.media_type == "text/csv"
    rows = list(csv.DictReader(StringIO(response.body.decode())))
    assert rows == [
        {
            "Project": "Matching export project",
            "Region": "Garki",
            "Created": "2026-04-20 12:00:00",
        }
    ]


def test_projects_export_csv_ignores_unknown_columns(monkeypatch, db_session, person):
    project = projects_service.projects.create(
        db_session,
        ProjectCreate(name="Column export project", status="active", priority="high"),
    )
    project.created_at = datetime(2026, 4, 20, 12, 0, tzinfo=UTC)
    db_session.commit()
    _stub_auth_helpers(monkeypatch, person)

    response = admin_projects.projects_export_csv(
        request=_make_request("/admin/projects/export.csv?columns=project,unknown,priority"),
        columns="project,unknown,priority",
        order_by="created_at",
        order_dir="desc",
        db=db_session,
    )

    rows = list(csv.DictReader(StringIO(response.body.decode())))
    assert list(rows[0].keys()) == ["Project", "Priority", "Created"]
    assert rows[0]["Project"] == "Column export project"
    assert rows[0]["Priority"] == "high"
