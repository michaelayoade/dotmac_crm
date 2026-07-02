"""Tests for the vendor-scoped fiber-plant map endpoint in the field API.

The nearby query itself relies on PostGIS (ST_DWithin/geography) and is covered
by the shared map-assets PG tests; here we lock down that the endpoint is
vendor-guarded and not reachable via a plain staff token.
"""

import pytest
from fastapi import HTTPException


def _walk(dependant):
    for dep in dependant.dependencies:
        yield dep
        yield from _walk(dep)


def test_route_uses_vendor_token_guard():
    from fastapi.routing import APIRoute

    from app.api.field.vendor_map import router
    from app.services.vendor_auth_tokens import require_vendor_token

    routes = [r for r in router.routes if isinstance(r, APIRoute)]
    assert len(routes) == 1
    for route in routes:
        found = any(dep.call is require_vendor_token for dep in _walk(route.dependant))
        assert found, f"{route.path} missing require_vendor_token"


def test_map_router_excluded_from_technician_guard():
    """The vendor map must not inherit require_technician from the field router."""
    from app.api.field import router as field_router
    from app.services.auth_dependencies import require_technician

    vendor_paths = {"/field/vendor/map-assets/nearby"}
    for route in field_router.routes:
        if getattr(route, "path", None) in vendor_paths:
            calls = [dep.call for dep in _walk(route.dependant)]
            assert require_technician not in calls


def test_nearby_rejects_non_vendor(db_session, person):
    from app.services.vendor_auth_tokens import require_vendor_token

    auth = {"person_id": str(person.id), "session_id": "s", "roles": [], "scopes": []}
    with pytest.raises(HTTPException) as exc:
        require_vendor_token(auth=auth, db=db_session)
    assert exc.value.status_code == 403
