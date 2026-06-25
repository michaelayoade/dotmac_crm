"""require_technician gates the staff field surface to actual technicians.

Regression for the surface where any authenticated user named on a work order —
not just a provisioned field technician — could reach the field app endpoints.
"""

import pytest
from fastapi import HTTPException

from app.models.rbac import PersonRole, Role
from app.services import auth_dependencies


def _auth(person, *, roles=None, scopes=None):
    return {
        "person_id": str(person.id),
        "session_id": "s",
        "roles": roles or [],
        "scopes": scopes or [],
    }


def test_non_technician_is_forbidden(db_session, person):
    with pytest.raises(HTTPException) as exc:
        auth_dependencies.require_technician(auth=_auth(person), db=db_session)
    assert exc.value.status_code == 403


def test_field_technician_role_claim_passes(db_session, person):
    result = auth_dependencies.require_technician(auth=_auth(person, roles=["field_technician"]), db=db_session)
    assert result["person_id"] == str(person.id)


def test_admin_passes(db_session, person):
    result = auth_dependencies.require_technician(auth=_auth(person, roles=["admin"]), db=db_session)
    assert result["roles"] == ["admin"]


def test_field_scope_claim_passes(db_session, person):
    result = auth_dependencies.require_technician(auth=_auth(person, scopes=["field:job:read"]), db=db_session)
    assert result["person_id"] == str(person.id)


def test_db_role_link_passes_when_claim_is_empty(db_session, person):
    role = db_session.query(Role).filter(Role.name == "field_technician").first()
    if not role:
        role = Role(name="field_technician", is_active=True)
        db_session.add(role)
        db_session.commit()
        db_session.refresh(role)
    db_session.add(PersonRole(person_id=person.id, role_id=role.id))
    db_session.commit()

    result = auth_dependencies.require_technician(auth=_auth(person), db=db_session)
    assert result["person_id"] == str(person.id)
