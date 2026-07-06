from pathlib import Path

CRITICAL_ACTION_TEMPLATES = [
    "templates/admin/system/users/edit.html",
    "templates/admin/system/roles_form.html",
    "templates/admin/projects/project_form.html",
    "templates/admin/projects/project_task_form.html",
    "templates/admin/projects/project_task_detail.html",
    "templates/admin/projects/tasks.html",
    "templates/admin/tickets/_form_body.html",
]


def test_critical_admin_action_forms_include_csrf_token():
    repo_root = Path(__file__).resolve().parents[1]
    for rel_path in CRITICAL_ACTION_TEMPLATES:
        content = (repo_root / rel_path).read_text(encoding="utf-8")
        has_csrf_include = '{% include "components/forms/csrf_input.html" %}' in content
        has_csrf_input = 'name="_csrf_token"' in content
        assert has_csrf_include or has_csrf_input, f"Missing CSRF token field in {rel_path}"


def test_crm_manager_popup_reassignment_fetch_sends_csrf_header():
    repo_root = Path(__file__).resolve().parents[1]
    content = (repo_root / "templates/admin/crm/inbox.html").read_text(encoding="utf-8")
    reassign_start = content.index("async reassignManagerChat")
    reassign_end = content.index("scheduleSummaryCountsRefresh", reassign_start)
    reassign_body = content[reassign_start:reassign_end]

    assert "const token = this.getCsrfToken();" in reassign_body
    assert "'X-CSRF-Token': token || ''" in reassign_body
