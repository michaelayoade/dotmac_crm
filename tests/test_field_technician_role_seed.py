"""The RBAC seed must create the field_technician role with its permission set."""

import importlib.util
from pathlib import Path

from app.models.rbac import Permission, Role, RolePermission

_SEED_PATH = Path(__file__).resolve().parents[1] / "scripts" / "seed_rbac.py"
_spec = importlib.util.spec_from_file_location("seed_rbac", _SEED_PATH)
seed_rbac = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed_rbac)


def _role_permission_keys(db_session, role: Role) -> set[str]:
    rows = (
        db_session.query(Permission.key)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .filter(RolePermission.role_id == role.id)
        .all()
    )
    return {key for (key,) in rows}


def test_seed_creates_field_technician_role_with_permissions(db_session):
    roles = seed_rbac.seed_roles_and_permissions(db_session)

    role = roles.get("field_technician")
    assert role is not None
    assert role.is_active

    granted = _role_permission_keys(db_session, role)
    assert granted == set(seed_rbac.FIELD_TECHNICIAN_PERMISSIONS)
    # The field tech role is minimal: no admin/staff write permissions leak in.
    assert "operations:work_order:delete" not in granted
    assert "system:settings:write" not in granted


def test_seed_is_idempotent(db_session):
    seed_rbac.seed_roles_and_permissions(db_session)
    role_count = db_session.query(Role).count()
    permission_count = db_session.query(Permission).count()
    link_count = db_session.query(RolePermission).count()

    seed_rbac.seed_roles_and_permissions(db_session)
    assert db_session.query(Role).count() == role_count
    assert db_session.query(Permission).count() == permission_count
    assert db_session.query(RolePermission).count() == link_count


def test_field_permissions_exist_after_seed(db_session):
    seed_rbac.seed_roles_and_permissions(db_session)
    for key in seed_rbac.FIELD_TECHNICIAN_PERMISSIONS:
        permission = db_session.query(Permission).filter(Permission.key == key).first()
        assert permission is not None, f"missing seeded permission: {key}"
        assert permission.is_active


def test_postpaid_customers_permission_exists_after_seed(db_session):
    seed_rbac.seed_roles_and_permissions(db_session)

    permission = db_session.query(Permission).filter(Permission.key == "reports:postpaid-customers:read").first()
    assert permission is not None
    assert permission.is_active
