"""/dispatch/* and /timecost/* must enforce permissions on every route.

Regression for the unscoped admin surface where any authenticated user could
edit technician schedules, skills, dispatch rules, cost/billing rates, and
worklogs.
"""

import inspect

from fastapi.routing import APIRoute

from app.api.dispatch import router as dispatch_router
from app.api.timecost import router as timecost_router

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _permission_keys_for_route(route: APIRoute) -> set[str]:
    keys: set[str] = set()
    for dependency in route.dependant.dependencies:
        call = dependency.call
        if getattr(call, "__name__", "") != "_require_permission":
            continue
        key = inspect.getclosurevars(call).nonlocals.get("permission_key")
        if key:
            keys.add(key)
    return keys


def _routes(router):
    return [r for r in router.routes if isinstance(r, APIRoute)]


def test_no_dispatch_or_timecost_route_is_unguarded():
    for router in (dispatch_router, timecost_router):
        for route in _routes(router):
            keys = _permission_keys_for_route(route)
            assert keys, f"{sorted(route.methods)} {route.path} has no require_permission guard"


def test_dispatch_mutations_require_technician_write():
    for route in _routes(dispatch_router):
        if (route.methods - {"HEAD", "OPTIONS"}) & _WRITE_METHODS and "auto-assign" not in route.path:
            keys = _permission_keys_for_route(route)
            assert "operations:technician:write" in keys, f"{route.path}: {keys}"


def test_dispatch_auto_assign_requires_dispatch_permission():
    route = next(r for r in _routes(dispatch_router) if r.path.endswith("/auto-assign"))
    assert "operations:work_order:dispatch" in _permission_keys_for_route(route)


def test_timecost_mutations_require_work_order_update():
    for route in _routes(timecost_router):
        if (route.methods - {"HEAD", "OPTIONS"}) & _WRITE_METHODS:
            keys = _permission_keys_for_route(route)
            assert "operations:work_order:update" in keys, f"{route.path}: {keys}"
