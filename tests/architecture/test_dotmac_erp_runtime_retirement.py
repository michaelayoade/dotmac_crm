"""CRM cannot regain a direct Dotmac ERP synchronization runtime.

CRM and ERP are independently deployed applications.  Retiring CRM's caller
means removing the executable client, its credentials/configuration, scheduled
work, admin controls, and application imports.  Historical ERP correlation and
sync-result columns remain admissible evidence; they are not executable paths.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = ROOT / ".env.example"
DIRECT_PACKAGE = ROOT / "app/services/dotmac_erp"
DIRECT_IMPORT = "app.services.dotmac_erp"
SETTING_PREFIX = "dotmac_erp_"
ENV_PREFIX = "DOTMAC_ERP_"
ROUTE_PREFIXES = ("/dotmac-erp", "/dotmac_erp")
HTTP_METHODS = frozenset({"delete", "get", "patch", "post", "put"})
LIVE_UI_FILES = tuple(sorted((ROOT / "templates").rglob("*.html")))
ROUTE_INVENTORIES = (
    ROOT / "docs/qa/reseller-access-FULL.json",
    ROOT / "docs/qa/route-sweep.json",
)
RETIREMENT_MIGRATION = ROOT / "alembic/versions/er2026082501_retire_direct_erp_runtime.py"
EXPECTED_RETIRED_TASK_NAMES = frozenset(
    {
        "app.tasks.integrations.detect_dotmac_erp_identity_drift",
        "app.tasks.integrations.redrive_failed_erp_pushes",
        "app.tasks.integrations.refresh_expense_request_erp_status",
        "app.tasks.integrations.refresh_material_request_erp_status",
        "app.tasks.integrations.refresh_pending_expense_request_erp_statuses",
        "app.tasks.integrations.refresh_pending_material_request_erp_statuses",
        "app.tasks.integrations.sync_dotmac_erp",
        "app.tasks.integrations.sync_dotmac_erp_agents",
        "app.tasks.integrations.sync_dotmac_erp_contacts",
        "app.tasks.integrations.sync_dotmac_erp_entity",
        "app.tasks.integrations.sync_dotmac_erp_inventory",
        "app.tasks.integrations.sync_dotmac_erp_shifts",
        "app.tasks.integrations.sync_dotmac_erp_teams",
        "app.tasks.integrations.sync_dotmac_erp_technicians",
        "app.tasks.integrations.sync_expense_request_to_erp",
        "app.tasks.integrations.sync_material_request_to_erp",
        "app.tasks.integrations.sync_purchase_invoice_to_erp",
        "app.tasks.integrations.sync_purchase_order_to_erp",
    }
)


def _direct_erp_runtime_violations(paths: list[Path]) -> list[str]:
    problems: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(DIRECT_IMPORT):
                problems.append(f"{path}: imports {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(DIRECT_IMPORT):
                        problems.append(f"{path}: imports {alias.name}")
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.startswith(SETTING_PREFIX):
                    problems.append(f"{path}: declares/reads retired setting {node.value}")
                elif node.value.startswith(ENV_PREFIX):
                    problems.append(f"{path}: declares/reads retired environment input {node.value}")
                elif any(prefix in node.value for prefix in ROUTE_PREFIXES):
                    problems.append(f"{path}: mounts retired route {node.value}")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "router"
                and node.func.attr in HTTP_METHODS
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and any(prefix in node.args[0].value for prefix in ROUTE_PREFIXES)
            ):
                problems.append(f"{path}: mounts retired route {node.args[0].value}")
    return problems


def test_crm_has_no_direct_dotmac_erp_runtime() -> None:
    assert not list(DIRECT_PACKAGE.glob("*.py")), "the retired direct ERP client/runtime still exists"

    sources = sorted((ROOT / "app").rglob("*.py"))
    assert sources, "the CRM runtime moved; this guard would pass vacuously"
    assert not _direct_erp_runtime_violations(sources)

    example = ENV_EXAMPLE.read_text(encoding="utf-8")
    forbidden_environment_inputs = (ENV_PREFIX, "ERPNEXT_URL", "ERPNEXT_API_KEY", "ERPNEXT_API_SECRET")
    assert not any(name in example for name in forbidden_environment_inputs), (
        "retired ERP credentials remain advertised in .env.example"
    )


def _retired_ui_route_violations(paths: tuple[Path, ...]) -> list[str]:
    return [str(path) for path in paths if any(prefix in path.read_text(encoding="utf-8") for prefix in ROUTE_PREFIXES)]


def test_retired_erp_route_is_absent_from_live_navigation_and_route_inventories() -> None:
    assert LIVE_UI_FILES, "the template tree moved; this guard would pass vacuously"
    assert all(path.is_file() for path in ROUTE_INVENTORIES), (
        "a checked route inventory moved without updating the guard"
    )
    assert not _retired_ui_route_violations((*LIVE_UI_FILES, *ROUTE_INVENTORIES))


def test_the_retirement_guard_bites_on_import_config_and_route(tmp_path: Path) -> None:
    source = tmp_path / "direct_erp.py"
    source.write_text(
        "from app.services.dotmac_erp.client import DotMacERPClient\n"
        "\n"
        "base_url = settings_spec.resolve_value(db, domain, 'dotmac_erp_base_url')\n"
        "token = os.getenv('DOTMAC_ERP_TOKEN')\n"
        "\n"
        "@router.post('/dotmac-erp/sync')\n"
        "def sync_now():\n"
        "    return DotMacERPClient(base_url, token)\n",
        encoding="utf-8",
    )

    problems = _direct_erp_runtime_violations([source])
    assert any("imports app.services.dotmac_erp.client" in item for item in problems)
    assert any("retired setting dotmac_erp_base_url" in item for item in problems)
    assert any("environment input DOTMAC_ERP_TOKEN" in item for item in problems)
    assert any("retired route /dotmac-erp/sync" in item for item in problems)

    live_template = tmp_path / "navigation.html"
    live_template.write_text('<a href="/dotmac-erp/sync">Direct ERP</a>', encoding="utf-8")
    assert _retired_ui_route_violations((live_template,)) == [str(live_template)]


def test_historical_erp_evidence_is_not_mistaken_for_a_runtime(tmp_path: Path) -> None:
    source = tmp_path / "history.py"
    source.write_text(
        "erpnext_id = observation.external_id\n"
        "erp_sync_status = historical_row.status\n"
        "erp_synced_at = historical_row.completed_at\n",
        encoding="utf-8",
    )
    assert not _direct_erp_runtime_violations([source])


def test_historical_erp_evidence_is_output_only() -> None:
    from app.schemas.service_team import ServiceTeamCreate, ServiceTeamUpdate
    from app.schemas.vendor import (
        InstallationProjectCreate,
        InstallationProjectUpdate,
        VendorCreate,
        VendorPurchaseInvoiceUpdate,
        VendorUpdate,
    )

    writable_fields = {
        "erp_id": (VendorCreate, VendorUpdate),
        "erp_department": (ServiceTeamCreate, ServiceTeamUpdate),
        "erp_purchase_order_id": (
            InstallationProjectCreate,
            InstallationProjectUpdate,
            VendorPurchaseInvoiceUpdate,
        ),
        "erp_purchase_invoice_id": (VendorPurchaseInvoiceUpdate,),
        "erp_sync_error": (VendorPurchaseInvoiceUpdate,),
        "erp_synced_at": (VendorPurchaseInvoiceUpdate,),
    }
    for field_name, schemas in writable_fields.items():
        assert all(field_name not in schema.model_fields for schema in schemas)


def _load_retirement_migration():
    spec = importlib.util.spec_from_file_location("retire_direct_erp_runtime", RETIREMENT_MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_retirement_migration_disables_persisted_work_and_redacts_credentials(monkeypatch) -> None:
    module = _load_retirement_migration()
    assert frozenset(module.RETIRED_TASK_NAMES) == EXPECTED_RETIRED_TASK_NAMES
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            sa.text("CREATE TABLE scheduled_tasks (task_name TEXT PRIMARY KEY, enabled BOOLEAN NOT NULL)")
        )
        connection.execute(
            sa.text(
                """
                CREATE TABLE domain_settings (
                    key TEXT PRIMARY KEY,
                    value_type TEXT NOT NULL,
                    value_text TEXT,
                    value_json TEXT,
                    is_secret BOOLEAN NOT NULL,
                    is_active BOOLEAN NOT NULL,
                    updated_at DATETIME
                )
                """
            )
        )
        for task_name in (*module.RETIRED_TASK_NAMES, "app.tasks.integrations.sync_chatwoot"):
            connection.execute(
                sa.text("INSERT INTO scheduled_tasks (task_name, enabled) VALUES (:task_name, true)"),
                {"task_name": task_name},
            )
        connection.execute(
            sa.text(
                """
                INSERT INTO domain_settings
                    (key, value_type, value_text, value_json, is_secret, is_active)
                VALUES
                    ('dotmac_erp_token', 'string', 'sensitive-material', NULL, true, true),
                    ('dotmac_erp_sync_enabled', 'boolean', 'true', NULL, false, true),
                    ('chatwoot_sync_enabled', 'boolean', 'true', NULL, false, true)
                """
            )
        )

        monkeypatch.setattr(module.op, "get_bind", lambda: connection)
        module.upgrade()
        module.upgrade()

        task_rows = {
            row.task_name: row.enabled
            for row in connection.execute(sa.text("SELECT task_name, enabled FROM scheduled_tasks")).mappings()
        }
        assert all(task_rows[name] == 0 for name in module.RETIRED_TASK_NAMES)
        assert task_rows["app.tasks.integrations.sync_chatwoot"] == 1

        setting_rows = {
            row.key: row
            for row in connection.execute(
                sa.text("SELECT key, value_type, value_text, value_json, is_secret, is_active FROM domain_settings")
            ).mappings()
        }
        for key in ("dotmac_erp_token", "dotmac_erp_sync_enabled"):
            row = setting_rows[key]
            assert row.value_type == "string"
            assert row.value_text == "retired"
            assert row.value_json is None
            assert row.is_secret == 0
            assert row.is_active == 0
        assert setting_rows["chatwoot_sync_enabled"].value_text == "true"
        assert setting_rows["chatwoot_sync_enabled"].is_active == 1


def test_retirement_migration_matches_current_models_and_runtime_registry() -> None:
    from app.celery_app import celery_app
    from app.models.domain_settings import DomainSetting
    from app.models.scheduler import ScheduledTask
    from app.tasks import integrations as _integrations  # noqa: F401

    scheduled_task_columns = set(ScheduledTask.__table__.c.keys())
    domain_setting_columns = set(DomainSetting.__table__.c.keys())
    assert {"task_name", "enabled"} <= scheduled_task_columns
    assert {
        "key",
        "value_type",
        "value_text",
        "value_json",
        "is_secret",
        "is_active",
        "updated_at",
    } <= domain_setting_columns
    assert not (EXPECTED_RETIRED_TASK_NAMES & celery_app.tasks.keys())
    assert "app.tasks.integrations.sync_chatwoot" in celery_app.tasks


def test_retirement_migration_refuses_a_false_downgrade() -> None:
    module = _load_retirement_migration()
    with pytest.raises(RuntimeError, match="irreversible"):
        module.downgrade()
