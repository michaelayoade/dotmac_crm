"""Tests for vendor crew quoting (bid) endpoints in the field API."""

from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models.vendor import (
    InstallationProject,
    InstallationProjectStatus,
    ProjectQuoteStatus,
    Vendor,
)
from app.schemas.vendor import QuoteLineItemCreateRequest
from app.services.field.vendor_quotes import field_vendor_quotes


@pytest.fixture()
def vendor(db_session):
    v = Vendor(name="FiberWorks Ltd", is_active=True)
    db_session.add(v)
    db_session.commit()
    db_session.refresh(v)
    return v


@pytest.fixture()
def other_vendor(db_session):
    v = Vendor(name="Rival Crew Co", is_active=True)
    db_session.add(v)
    db_session.commit()
    db_session.refresh(v)
    return v


@pytest.fixture()
def installation_project(db_session, vendor, project):
    ip = InstallationProject(
        project_id=project.id,
        assigned_vendor_id=vendor.id,
        status=InstallationProjectStatus.in_progress,
    )
    db_session.add(ip)
    db_session.commit()
    db_session.refresh(ip)
    return ip


def _line_item(**overrides) -> QuoteLineItemCreateRequest:
    data = {"description": "Trenching 100m", "quantity": Decimal("2"), "unit_price": Decimal("5000.00")}
    data.update(overrides)
    return QuoteLineItemCreateRequest(**data)


def test_open_draft_creates_scoped_quote(db_session, vendor, person, installation_project):
    quote = field_vendor_quotes.open_draft(db_session, str(vendor.id), str(installation_project.id), str(person.id))
    assert quote.status == ProjectQuoteStatus.draft
    assert str(quote.vendor_id) == str(vendor.id)
    # Idempotent: reopening returns the same draft, not a second one.
    again = field_vendor_quotes.open_draft(db_session, str(vendor.id), str(installation_project.id), str(person.id))
    assert again.id == quote.id


def test_add_line_item_recalculates_and_lists(db_session, vendor, person, installation_project):
    quote = field_vendor_quotes.open_draft(db_session, str(vendor.id), str(installation_project.id), str(person.id))
    item = field_vendor_quotes.add_line_item(db_session, str(vendor.id), str(quote.id), _line_item())
    assert item.amount == Decimal("10000.00")  # 2 * 5000

    bundle = field_vendor_quotes.get_detail(db_session, str(vendor.id), str(quote.id))
    assert len(bundle["line_items"]) == 1
    assert bundle["quote"].total == Decimal("10000.00")


def test_get_detail_404_for_other_vendor(db_session, vendor, other_vendor, person, installation_project):
    quote = field_vendor_quotes.open_draft(db_session, str(vendor.id), str(installation_project.id), str(person.id))
    with pytest.raises(HTTPException) as exc:
        field_vendor_quotes.get_detail(db_session, str(other_vendor.id), str(quote.id))
    assert exc.value.status_code == 404


def test_remove_line_item(db_session, vendor, person, installation_project):
    quote = field_vendor_quotes.open_draft(db_session, str(vendor.id), str(installation_project.id), str(person.id))
    item = field_vendor_quotes.add_line_item(db_session, str(vendor.id), str(quote.id), _line_item())
    field_vendor_quotes.remove_line_item(db_session, str(vendor.id), str(quote.id), str(item.id))
    bundle = field_vendor_quotes.get_detail(db_session, str(vendor.id), str(quote.id))
    assert bundle["line_items"] == []


def test_submit_requires_a_line_item(db_session, vendor, person, installation_project):
    quote = field_vendor_quotes.open_draft(db_session, str(vendor.id), str(installation_project.id), str(person.id))
    with pytest.raises(HTTPException) as exc:
        field_vendor_quotes.submit(db_session, str(vendor.id), str(quote.id))
    assert exc.value.status_code == 400


def test_submit_transitions_to_submitted(db_session, vendor, person, installation_project):
    quote = field_vendor_quotes.open_draft(db_session, str(vendor.id), str(installation_project.id), str(person.id))
    field_vendor_quotes.add_line_item(db_session, str(vendor.id), str(quote.id), _line_item())
    submitted = field_vendor_quotes.submit(db_session, str(vendor.id), str(quote.id))
    assert submitted.status == ProjectQuoteStatus.submitted
    assert submitted.submitted_at is not None

    mine = field_vendor_quotes.list_mine(db_session, str(vendor.id))
    assert [q.id for q in mine] == [quote.id]


def test_list_scoped_to_vendor(db_session, vendor, other_vendor, person, installation_project):
    field_vendor_quotes.open_draft(db_session, str(vendor.id), str(installation_project.id), str(person.id))
    assert len(field_vendor_quotes.list_mine(db_session, str(vendor.id))) == 1
    assert field_vendor_quotes.list_mine(db_session, str(other_vendor.id)) == []


def _walk(dependant):
    for dep in dependant.dependencies:
        yield dep
        yield from _walk(dep)


def test_routes_use_vendor_token_guard():
    from fastapi.routing import APIRoute

    from app.api.field.vendor_quotes import router
    from app.services.vendor_auth_tokens import require_vendor_token

    routes = [r for r in router.routes if isinstance(r, APIRoute)]
    assert len(routes) == 6
    for route in routes:
        found = any(dep.call is require_vendor_token for dep in _walk(route.dependant))
        assert found, f"{route.path} missing require_vendor_token"
