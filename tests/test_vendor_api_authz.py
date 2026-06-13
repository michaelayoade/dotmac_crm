"""The /vendors admin API must enforce vendor permissions and derive the
reviewer identity from the authenticated caller.

Regression for the unscoped surface where any authenticated principal
(including vendor-portal users) could assign projects to a vendor, and
self-approve / reject / accept its own quotes by supplying an arbitrary
``reviewer_person_id`` in the request — a financial-control bypass and an
audit-trail forgery.
"""

import inspect

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.api.vendors import accept_as_built, approve_quote, reject_quote
from app.api.vendors import router as vendors_router
from app.models.rbac import Permission
from app.schemas.vendor import (
    InstallationProjectCreate,
    ProjectQuoteCreate,
    QuoteApprovalRequest,
    QuoteLineItemCreate,
    QuoteRejectRequest,
    VendorCreate,
)
from app.services import auth_dependencies
from app.services import vendor as vendor_service

EXPECTED_PERMISSIONS = {
    ("POST", "/vendors"): "vendor:write",
    ("GET", "/vendors"): "vendor:read",
    ("GET", "/vendors/{vendor_id}"): "vendor:read",
    ("PATCH", "/vendors/{vendor_id}"): "vendor:write",
    ("DELETE", "/vendors/{vendor_id}"): "vendor:write",
    ("POST", "/vendors/projects"): "vendor:project:write",
    ("POST", "/vendors/projects/{project_id}/open-bidding"): "vendor:project:write",
    ("POST", "/vendors/projects/{project_id}/assign/{vendor_id}"): "vendor:project:write",
    ("POST", "/vendors/quotes/{quote_id}/approve"): "vendor:project:write",
    ("POST", "/vendors/quotes/{quote_id}/reject"): "vendor:project:write",
    ("POST", "/vendors/as-built/{as_built_id}/accept"): "vendor:project:write",
    ("GET", "/vendors/as-built/{as_built_id}/compare"): "vendor:project:read",
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


def test_every_vendor_admin_route_requires_the_expected_permission():
    seen: set[tuple[str, str]] = set()
    for route in vendors_router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            seen.add((method, route.path))
            expected = EXPECTED_PERMISSIONS.get((method, route.path))
            assert expected is not None, f"Unexpected unmapped route: {method} {route.path}"
            keys = _permission_keys_for_route(route)
            assert expected in keys, f"{method} {route.path} missing require_permission({expected!r}); has {keys}"
    assert seen == set(EXPECTED_PERMISSIONS), f"Route inventory drifted: {seen ^ set(EXPECTED_PERMISSIONS)}"


def test_reviewer_identity_is_not_accepted_from_the_request():
    # The approve/reject schemas must not carry a caller-supplied reviewer,
    # and the as-built accept route must not take a reviewer query parameter.
    assert "reviewer_person_id" not in QuoteApprovalRequest.model_fields
    assert "reviewer_person_id" not in QuoteRejectRequest.model_fields
    assert "reviewer_id" not in inspect.signature(accept_as_built).parameters
    assert "reviewer_person_id" not in inspect.signature(approve_quote).parameters
    assert "reviewer_person_id" not in inspect.signature(reject_quote).parameters


def _seed_permission(db_session, key: str) -> Permission:
    permission = db_session.query(Permission).filter(Permission.key == key).first()
    if not permission:
        permission = Permission(key=key, description="test", is_active=True)
        db_session.add(permission)
        db_session.commit()
        db_session.refresh(permission)
    return permission


def test_user_without_vendor_permission_is_forbidden(db_session, person):
    _seed_permission(db_session, "vendor:project:write")
    guard = auth_dependencies.require_permission("vendor:project:write")

    auth = {"person_id": str(person.id), "session_id": "s", "roles": [], "scopes": []}
    with pytest.raises(HTTPException) as exc:
        guard(auth=auth, db=db_session)
    assert exc.value.status_code == 403


def _submitted_quote(db_session, person, project):
    vendor = vendor_service.vendors.create(db_session, VendorCreate(name="Acme Vendor"))
    installation_project = vendor_service.installation_projects.create(
        db_session,
        InstallationProjectCreate(project_id=project.id, assigned_vendor_id=vendor.id),
    )
    quote = vendor_service.project_quotes.create(
        db_session,
        ProjectQuoteCreate(project_id=installation_project.id),
        vendor_id=str(vendor.id),
        created_by_person_id=str(person.id),
    )
    from decimal import Decimal

    vendor_service.quote_line_items.create(
        db_session,
        QuoteLineItemCreate(
            quote_id=quote.id,
            item_type="labor",
            description="Installation labor",
            quantity=Decimal("1.000"),
            unit_price=Decimal("1000.00"),
        ),
        vendor_id=str(vendor.id),
    )
    vendor_service.project_quotes.submit(db_session, str(quote.id), vendor_id=str(vendor.id))
    return quote


def test_approve_records_reviewer_from_auth_not_request(db_session, person, project, monkeypatch):
    monkeypatch.setattr(vendor_service, "_geojson_to_geom", lambda _geojson: None)
    quote = _submitted_quote(db_session, person, project)

    auth = {"person_id": str(person.id), "session_id": "s", "roles": ["admin"], "scopes": []}
    result = approve_quote(
        quote_id=str(quote.id),
        payload=QuoteApprovalRequest(review_notes="ok", override_threshold=True),
        db=db_session,
        auth=auth,
    )

    # The reviewer recorded on the quote is the authenticated caller — there is
    # no longer any request field that could override it.
    assert str(result.reviewed_by_person_id) == str(person.id)
