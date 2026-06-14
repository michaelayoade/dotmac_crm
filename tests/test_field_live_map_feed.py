"""The admin field-tech live-map feed + page must be permission-gated (tasks #43/#44)."""

import inspect

from fastapi.routing import APIRoute

from app.web.admin.operations import router as operations_router

EXPECTED = {
    ("GET", "/operations/field-techs/live-map"): "operations:work_order:read",
    ("GET", "/operations/field-techs/map"): "operations:work_order:read",
}


def _permission_keys(route: APIRoute) -> set[str]:
    keys: set[str] = set()
    for dependency in route.dependant.dependencies:
        call = dependency.call
        if getattr(call, "__name__", "") != "_require_permission":
            continue
        key = inspect.getclosurevars(call).nonlocals.get("permission_key")
        if key:
            keys.add(key)
    return keys


def test_field_live_map_routes_are_permission_gated():
    found = {}
    for route in operations_router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            if (method, route.path) in EXPECTED:
                found[(method, route.path)] = _permission_keys(route)

    for key, expected_perm in EXPECTED.items():
        assert key in found, f"Route not registered: {key}"
        assert expected_perm in found[key], f"{key} missing require_permission({expected_perm!r})"
