"""The retired legacy ERPNext importer cannot return through another adapter.

Dotmac ERP is the supported application-to-application sync peer.  CRM's old
one-time ERPNext importer was a separate provider runtime: it accepted provider
credentials, tested them from a web request, called Frappe directly, and wrote
CRM domain rows.  Retiring it means deleting that executable boundary while
preserving ``erpnext_id`` only as historical correlation data.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEGACY_PACKAGE = ROOT / "app/services/erpnext"
LEGACY_TEMPLATE = ROOT / "templates/admin/integrations/erpnext"
LEGACY_IMPORT = "app.services.erpnext"
LEGACY_RUNTIME_NAMES = frozenset(
    {
        "ERPNextClient",
        "ERPNextImporter",
        "erpnext_api_key",
        "erpnext_api_secret",
        "erpnext_url",
    }
)
LEGACY_ENV_NAMES = frozenset({"ERPNEXT_API_KEY", "ERPNEXT_API_SECRET", "ERPNEXT_URL"})
HTTP_METHODS = frozenset({"delete", "get", "patch", "post", "put"})


def _runtime_violations(paths: list[Path]) -> list[str]:
    problems: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(LEGACY_IMPORT):
                problems.append(f"{path}: imports {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(LEGACY_IMPORT):
                        problems.append(f"{path}: imports {alias.name}")
            elif isinstance(node, ast.Name) and node.id in LEGACY_RUNTIME_NAMES:
                problems.append(f"{path}: references {node.id}")
            elif isinstance(node, ast.Attribute) and node.attr in LEGACY_RUNTIME_NAMES:
                problems.append(f"{path}: references {node.attr}")
            elif isinstance(node, ast.Call):
                if (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "router"
                    and node.func.attr in HTTP_METHODS
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and node.args[0].value.startswith("/erpnext")
                ):
                    problems.append(f"{path}: mounts {node.args[0].value}")
                if (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                    and node.func.attr == "getenv"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value in LEGACY_ENV_NAMES
                ):
                    problems.append(f"{path}: reads {node.args[0].value}")
    return problems


def test_the_legacy_erpnext_import_runtime_is_absent() -> None:
    assert not list(LEGACY_PACKAGE.glob("*.py")), (
        "the legacy ERPNext provider client/importer still exists; Dotmac ERP is the supported app-to-app sync peer"
    )
    assert not list(LEGACY_TEMPLATE.rglob("*")), "the retired ERPNext credential/import surface is still published"

    sources = sorted((ROOT / "app").rglob("*.py"))
    assert sources, "the CRM runtime source moved; this guard would pass vacuously"
    assert not _runtime_violations(sources)


def test_the_retirement_guard_bites_on_an_import_route_and_secret(tmp_path: Path) -> None:
    source = tmp_path / "legacy.py"
    source.write_text(
        "import os\n"
        "from app.services.erpnext import ERPNextClient\n"
        "\n"
        "erpnext_api_secret = os.getenv('ERPNEXT_API_SECRET')\n"
        "\n"
        "@router.post('/erpnext/import')\n"
        "def import_now():\n"
        "    return ERPNextClient\n",
        encoding="utf-8",
    )

    problems = _runtime_violations([source])
    assert any("imports app.services.erpnext" in item for item in problems)
    assert any("mounts /erpnext/import" in item for item in problems)
    assert any("reads ERPNEXT_API_SECRET" in item for item in problems)


def test_historical_erpnext_ids_are_not_mistaken_for_a_runtime() -> None:
    tree = ast.parse("erpnext_id = observation.external_id\n")
    assert tree.body, "the specificity probe is malformed"
    # The scanner reads a file; prove the allowed spelling independently so
    # this test never writes a fake source into the repository.
    assert "erpnext_id" not in LEGACY_RUNTIME_NAMES
