"""The workforce API must enforce operations:work_order:* permissions.

Regression for the unscoped surface where any authenticated user could
read, modify, and delete any work order.
"""

import inspect

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.api.workforce import router as workforce_router
from app.models.rbac import Permission, PersonRole, Role, RolePermission
from app.services import auth_dependencies

EXPECTED_PERMISSIONS = {
    ("POST", "/work-orders"): "operations:work_order:create",
    ("GET", "/work-orders"): "operations:work_order:read",
    ("GET", "/work-orders/{work_order_id}"): "operations:work_order:read",
    ("PATCH", "/work-orders/{work_order_id}"): "operations:work_order:update",
    ("DELETE", "/work-orders/{work_order_id}"): "operations:work_order:delete",
    ("GET", "/work-orders/{work_order_id}/cost-summary"): "operations:work_order:read",
    ("POST", "/work-order-assignments"): "operations:work_order:update",
    ("GET", "/work-order-assignments"): "operations:work_order:read",
    ("GET", "/work-order-assignments/{assignment_id}"): "operations:work_order:read",
    ("PATCH", "/work-order-assignments/{assignment_id}"): "operations:work_order:update",
    ("DELETE", "/work-order-assignments/{assignment_id}"): "operations:work_order:update",
    ("POST", "/work-order-notes"): "operations:work_order:update",
    ("GET", "/work-order-notes"): "operations:work_order:read",
    ("GET", "/work-order-notes/{note_id}"): "operations:work_order:read",
    ("PATCH", "/work-order-notes/{note_id}"): "operations:work_order:update",
    ("DELETE", "/work-order-notes/{note_id}"): "operations:work_order:update",
}


def _permission_keys_for_route(route: APIRoute) -> set[str]:
    keys: set[str] = set()
    for dependency in route.dependant.dependencies:
        call = dependency.call
        if call is None or not callable(call):
            continue
        if getattr(call, "__name__", "") != "_require_permission":
            continue
        closure_vars = inspect.getclosurevars(call)
        key = closure_vars.nonlocals.get("permission_key")
        if key:
            keys.add(key)
    return keys


def test_every_workforce_route_requires_the_expected_permission():
    seen: set[tuple[str, str]] = set()
    for route in workforce_router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            seen.add((method, route.path))
            expected = EXPECTED_PERMISSIONS.get((method, route.path))
            assert expected is not None, f"Unexpected unmapped route: {method} {route.path}"
            keys = _permission_keys_for_route(route)
            assert expected in keys, f"{method} {route.path} missing require_permission({expected!r}); has {keys}"
    assert seen == set(EXPECTED_PERMISSIONS), f"Route inventory drifted: {seen ^ set(EXPECTED_PERMISSIONS)}"


def _seed_permission(db_session, key: str) -> Permission:
    permission = db_session.query(Permission).filter(Permission.key == key).first()
    if not permission:
        permission = Permission(key=key, description="test", is_active=True)
        db_session.add(permission)
        db_session.commit()
        db_session.refresh(permission)
    return permission


def test_user_without_permission_is_forbidden(db_session, person):
    _seed_permission(db_session, "operations:work_order:read")
    guard = auth_dependencies.require_permission("operations:work_order:read")

    auth = {"person_id": str(person.id), "session_id": "s", "roles": [], "scopes": []}
    with pytest.raises(HTTPException) as exc:
        guard(auth=auth, db=db_session)
    assert exc.value.status_code == 403


def test_user_with_role_permission_passes(db_session, person):
    permission = _seed_permission(db_session, "operations:work_order:read")
    role = Role(name=f"wo-reader-{person.id.hex[:8]}", is_active=True)
    db_session.add(role)
    db_session.commit()
    db_session.add(RolePermission(role_id=role.id, permission_id=permission.id))
    db_session.add(PersonRole(person_id=person.id, role_id=role.id))
    db_session.commit()

    guard = auth_dependencies.require_permission("operations:work_order:read")
    auth = {"person_id": str(person.id), "session_id": "s", "roles": [], "scopes": []}
    result = guard(auth=auth, db=db_session)
    assert result["person_id"] == str(person.id)


def test_admin_role_bypasses_permission_check(db_session, person):
    guard = auth_dependencies.require_permission("operations:work_order:delete")
    auth = {"person_id": str(person.id), "session_id": "s", "roles": ["admin"], "scopes": []}
    result = guard(auth=auth, db=db_session)
    assert result["roles"] == ["admin"]
