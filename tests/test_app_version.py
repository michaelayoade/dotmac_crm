import tomllib
from pathlib import Path

from app.version import get_app_version
from app.web.templates import Jinja2Templates


def _pyproject_version() -> str:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return str(data["tool"]["poetry"]["version"])


def test_app_version_reads_pyproject_version():
    get_app_version.cache_clear()

    assert get_app_version() == _pyproject_version()


def test_templates_expose_app_version_global():
    get_app_version.cache_clear()
    templates = Jinja2Templates(directory="templates")

    assert templates.env.globals["app_version"] == _pyproject_version()
