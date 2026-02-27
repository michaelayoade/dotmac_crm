import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest


def _stubbed_web_auth_dependencies() -> dict[str, ModuleType]:
    fastapi_module = ModuleType("fastapi")
    fastapi_module.__path__ = []
    fastapi_module.Request = object

    fastapi_responses = ModuleType("fastapi.responses")
    fastapi_responses.HTMLResponse = object
    fastapi_responses.RedirectResponse = object

    fastapi_templating = ModuleType("fastapi.templating")
    fastapi_templating.Jinja2Templates = lambda *args, **kwargs: object()

    sqlalchemy_orm = ModuleType("sqlalchemy.orm")
    sqlalchemy_orm.Session = object
    sqlalchemy_module = ModuleType("sqlalchemy")
    sqlalchemy_module.__path__ = []

    app_config = ModuleType("app.config")
    app_config.settings = SimpleNamespace(cookie_secure=True)

    app_services = ModuleType("app.services")
    app_services.__path__ = []
    app_services.auth_flow = SimpleNamespace(auth_flow=SimpleNamespace())

    app_services_auth_flow = ModuleType("app.services.auth_flow")
    app_services_auth_flow.AuthFlow = object

    app_services_email = ModuleType("app.services.email")
    app_services_email.send_password_reset_email = lambda *args, **kwargs: None
    return {
        "fastapi": fastapi_module,
        "fastapi.responses": fastapi_responses,
        "fastapi.templating": fastapi_templating,
        "sqlalchemy": sqlalchemy_module,
        "sqlalchemy.orm": sqlalchemy_orm,
        "app.config": app_config,
        "app.services": app_services,
        "app.services.auth_flow": app_services_auth_flow,
        "app.services.email": app_services_email,
    }


def _load_web_auth_module():
    module_name = "tests._web_auth_under_test"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached

    module_path = Path(__file__).resolve().parents[1] / "app/services/web_auth.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise AssertionError("Unable to load app/services/web_auth.py for testing")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    with patch.dict(sys.modules, _stubbed_web_auth_dependencies()):
        spec.loader.exec_module(module)
    return module


_sanitize_refresh_next = _load_web_auth_module()._sanitize_refresh_next


def test_sanitize_refresh_next_allows_internal_relative_path():
    assert _sanitize_refresh_next("/dashboard", "/admin/dashboard") == "/dashboard"


@pytest.mark.parametrize(
    "next_url",
    [
        "//attacker.com",
        "https://evil.com",
        "javascript:alert(1)",
    ],
)
def test_sanitize_refresh_next_rejects_external_targets(next_url: str):
    assert _sanitize_refresh_next(next_url, "/admin/dashboard") == "/admin/dashboard"


def test_sanitize_refresh_next_inbox_unsafe_segment_redirects_to_inbox_root():
    assert (
        _sanitize_refresh_next("/admin/crm/inbox/convert?conversation=1", "/admin/dashboard")
        == "/admin/crm/inbox?conversation=1"
    )


def test_sanitize_refresh_next_contacts_unsafe_segment_redirects_to_contacts_root():
    assert _sanitize_refresh_next("/admin/crm/contacts/delete?person=1", "/admin/dashboard") == "/admin/crm/contacts"
