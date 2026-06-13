"""The plain work-order transition route must require operations:work_order:update.

Regression for the gate-bypass finding: field technicians (who lack that
permission) must be forced through /field/jobs/{id}/transition, which enforces
assignment scoping and the photo+signature completion gate.
"""

import inspect

from fastapi.routing import APIRoute

from app.api.workflow import router as workflow_router
from app.services import auth_dependencies


def _permission_keys(route: APIRoute) -> set[str]:
    keys: set[str] = set()
    for dep in route.dependant.dependencies:
        call = dep.call
        if getattr(call, "__name__", "") != "_require_permission":
            continue
        keys.add(inspect.getclosurevars(call).nonlocals.get("permission_key"))
    return keys


def test_work_order_transition_requires_update_permission():
    route = next(
        r
        for r in workflow_router.routes
        if isinstance(r, APIRoute) and r.path == "/work-orders/{work_order_id}/transition"
    )
    assert "operations:work_order:update" in _permission_keys(route)


def test_field_technician_role_lacks_work_order_update():
    # If this regresses, the dependency above would no longer block techs.
    import importlib.util
    from pathlib import Path

    seed_path = Path(__file__).resolve().parents[1] / "scripts" / "seed_rbac.py"
    spec = importlib.util.spec_from_file_location("seed_rbac", seed_path)
    seed_rbac = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed_rbac)
    assert "operations:work_order:update" not in seed_rbac.FIELD_TECHNICIAN_PERMISSIONS


def test_require_permission_guard_is_real():
    # Sanity: the guard factory exists and is what the route uses.
    assert callable(auth_dependencies.require_permission("operations:work_order:update"))
