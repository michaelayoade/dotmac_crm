from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.schemas.projects import ProjectCreate
from app.schemas.vendor import (
    InstallationProjectCreate,
    ProjectQuoteCreate,
    QuoteLineItemCreate,
    VendorCreate,
    VendorPurchaseInvoiceCreate,
    VendorPurchaseInvoiceLineItemCreate,
    VendorPurchaseInvoiceLineItemUpdate,
)
from app.services import projects as projects_service
from app.services import vendor as vendor_service


def _setup_vendor_project_quote(db_session, project):
    vendor = vendor_service.vendors.create(db_session, VendorCreate(name="Acme Vendor"))
    installation_project = vendor_service.installation_projects.create(
        db_session,
        InstallationProjectCreate(
            project_id=project.id,
            assigned_vendor_id=vendor.id,
        ),
    )
    quote = vendor_service.project_quotes.create(
        db_session,
        ProjectQuoteCreate(project_id=installation_project.id),
        vendor_id=str(vendor.id),
        created_by_person_id=None,
    )
    return vendor, installation_project, quote


def _submit_quote(db_session, vendor_id: str, quote_id: str, quote_uuid):
    vendor_service.quote_line_items.create(
        db_session,
        QuoteLineItemCreate(
            quote_id=quote_uuid,
            item_type="labor",
            description="Installation labor",
            quantity=Decimal("1.000"),
            unit_price=Decimal("100.00"),
        ),
        vendor_id=vendor_id,
    )
    vendor_service.project_quotes.submit(db_session, quote_id, vendor_id=vendor_id)


def test_purchase_invoice_create_requires_submitted_quote(db_session, project):
    vendor, installation_project, _quote = _setup_vendor_project_quote(db_session, project)

    with pytest.raises(HTTPException) as excinfo:
        vendor_service.vendor_purchase_invoices.create(
            db_session,
            VendorPurchaseInvoiceCreate(project_id=installation_project.id),
            vendor_id=str(vendor.id),
            created_by_person_id=None,
        )

    assert excinfo.value.status_code == 400
    assert "submitted vendor quote is required" in str(excinfo.value.detail).lower()


def test_purchase_invoice_create_allows_one_invoice_per_vendor_project(db_session, project):
    vendor, installation_project, quote = _setup_vendor_project_quote(db_session, project)
    _submit_quote(db_session, str(vendor.id), str(quote.id), quote.id)

    invoice = vendor_service.vendor_purchase_invoices.create(
        db_session,
        VendorPurchaseInvoiceCreate(project_id=installation_project.id),
        vendor_id=str(vendor.id),
        created_by_person_id=None,
    )

    assert str(invoice.project_id) == str(installation_project.id)
    assert str(invoice.vendor_id) == str(vendor.id)
    assert invoice.invoice_number == "INV-0001"

    with pytest.raises(HTTPException) as excinfo:
        vendor_service.vendor_purchase_invoices.create(
            db_session,
            VendorPurchaseInvoiceCreate(project_id=installation_project.id),
            vendor_id=str(vendor.id),
            created_by_person_id=None,
        )

    assert excinfo.value.status_code == 400
    assert "already exists" in str(excinfo.value.detail).lower()


def test_purchase_invoice_inherits_project_erp_po_id(db_session, project):
    vendor, installation_project, quote = _setup_vendor_project_quote(db_session, project)
    _submit_quote(db_session, str(vendor.id), str(quote.id), quote.id)
    installation_project.erp_purchase_order_id = "PO-2026-00045"
    db_session.commit()

    invoice = vendor_service.vendor_purchase_invoices.create(
        db_session,
        VendorPurchaseInvoiceCreate(project_id=installation_project.id),
        vendor_id=str(vendor.id),
        created_by_person_id=None,
    )

    assert invoice.erp_purchase_order_id == "PO-2026-00045"


def test_purchase_invoice_submit_requires_line_items_even_with_attachment(db_session, project, monkeypatch):
    vendor, installation_project, quote = _setup_vendor_project_quote(db_session, project)
    _submit_quote(db_session, str(vendor.id), str(quote.id), quote.id)
    invoice = vendor_service.vendor_purchase_invoices.create(
        db_session,
        VendorPurchaseInvoiceCreate(project_id=installation_project.id),
        vendor_id=str(vendor.id),
        created_by_person_id=None,
    )

    stored: dict[str, bytes] = {}

    monkeypatch.setattr(
        vendor_service.storage,
        "put",
        lambda key, data, content_type="": stored.setdefault(key, data) or f"/storage/{key}",
    )
    monkeypatch.setattr(vendor_service.storage, "delete", lambda key: stored.pop(key, None))

    vendor_service.vendor_purchase_invoices.upload_attachment(
        db_session,
        str(invoice.id),
        file_name="invoice.pdf",
        mime_type="application/pdf",
        file_content=b"pdf-bytes",
        vendor_id=str(vendor.id),
    )

    with pytest.raises(HTTPException) as excinfo:
        vendor_service.vendor_purchase_invoices.submit(db_session, str(invoice.id), vendor_id=str(vendor.id))

    assert excinfo.value.status_code == 400
    assert "at least one active line item" in str(excinfo.value.detail).lower()


def test_purchase_invoice_submit_locks_future_edits(db_session, project):
    vendor, installation_project, quote = _setup_vendor_project_quote(db_session, project)
    _submit_quote(db_session, str(vendor.id), str(quote.id), quote.id)
    invoice = vendor_service.vendor_purchase_invoices.create(
        db_session,
        VendorPurchaseInvoiceCreate(project_id=installation_project.id),
        vendor_id=str(vendor.id),
        created_by_person_id=None,
    )
    item = vendor_service.vendor_purchase_invoice_line_items.create(
        db_session,
        VendorPurchaseInvoiceLineItemCreate(
            invoice_id=invoice.id,
            item_type="material",
            description="Drop cable",
            quantity=Decimal("2.000"),
            unit_price=Decimal("50.00"),
        ),
        vendor_id=str(vendor.id),
    )

    submitted = vendor_service.vendor_purchase_invoices.submit(db_session, str(invoice.id), vendor_id=str(vendor.id))
    assert submitted.status.value == "submitted"

    with pytest.raises(HTTPException) as excinfo:
        vendor_service.vendor_purchase_invoice_line_items.update(
            db_session,
            invoice_id=str(invoice.id),
            line_item_id=str(item.id),
            payload=VendorPurchaseInvoiceLineItemUpdate(description="Updated after submit"),
            vendor_id=str(vendor.id),
        )

    assert excinfo.value.status_code == 400
    assert "draft or revision requested" in str(excinfo.value.detail).lower()


def test_purchase_invoice_tax_rate_updates_totals_from_subtotal(db_session, project):
    vendor, installation_project, quote = _setup_vendor_project_quote(db_session, project)
    _submit_quote(db_session, str(vendor.id), str(quote.id), quote.id)
    invoice = vendor_service.vendor_purchase_invoices.create(
        db_session,
        VendorPurchaseInvoiceCreate(project_id=installation_project.id),
        vendor_id=str(vendor.id),
        created_by_person_id=None,
    )
    vendor_service.vendor_purchase_invoice_line_items.create(
        db_session,
        VendorPurchaseInvoiceLineItemCreate(
            invoice_id=invoice.id,
            item_type="material",
            description="Cable",
            quantity=Decimal("2.000"),
            unit_price=Decimal("100.00"),
        ),
        vendor_id=str(vendor.id),
    )

    updated = vendor_service.vendor_purchase_invoices.set_tax_rate(
        db_session,
        invoice_id=str(invoice.id),
        vendor_id=str(vendor.id),
        tax_rate_percent=Decimal("7.50"),
    )

    assert updated.tax_rate_percent == Decimal("7.50")
    assert updated.subtotal == Decimal("200.00")
    assert updated.tax_total == Decimal("15.00")
    assert updated.total == Decimal("215.00")


def test_purchase_invoice_number_increments_globally(db_session, project):
    vendor, installation_project, quote = _setup_vendor_project_quote(db_session, project)
    _submit_quote(db_session, str(vendor.id), str(quote.id), quote.id)
    first = vendor_service.vendor_purchase_invoices.create(
        db_session,
        VendorPurchaseInvoiceCreate(project_id=installation_project.id),
        vendor_id=str(vendor.id),
        created_by_person_id=None,
    )
    first.is_active = False
    db_session.commit()

    second_base_project = projects_service.projects.create(
        db_session,
        ProjectCreate(name="Second Fiber Rollout"),
    )
    second_project = vendor_service.installation_projects.create(
        db_session,
        InstallationProjectCreate(
            project_id=second_base_project.id,
            assigned_vendor_id=vendor.id,
        ),
    )
    second_quote = vendor_service.project_quotes.create(
        db_session,
        ProjectQuoteCreate(project_id=second_project.id),
        vendor_id=str(vendor.id),
        created_by_person_id=None,
    )
    _submit_quote(db_session, str(vendor.id), str(second_quote.id), second_quote.id)

    second = vendor_service.vendor_purchase_invoices.create(
        db_session,
        VendorPurchaseInvoiceCreate(project_id=second_project.id),
        vendor_id=str(vendor.id),
        created_by_person_id=None,
    )

    assert first.invoice_number == "INV-0001"
    assert second.invoice_number == "INV-0002"


def test_purchase_invoice_approve_requires_submitted_state(db_session, project, person):
    vendor, installation_project, quote = _setup_vendor_project_quote(db_session, project)
    _submit_quote(db_session, str(vendor.id), str(quote.id), quote.id)
    invoice = vendor_service.vendor_purchase_invoices.create(
        db_session,
        VendorPurchaseInvoiceCreate(project_id=installation_project.id),
        vendor_id=str(vendor.id),
        created_by_person_id=None,
    )

    with pytest.raises(HTTPException) as excinfo:
        vendor_service.vendor_purchase_invoices.approve(
            db_session,
            invoice_id=str(invoice.id),
            reviewer_person_id=str(person.id),
            review_notes=None,
        )

    assert excinfo.value.status_code == 400
    assert "only submitted purchase invoices can be approved" in str(excinfo.value.detail).lower()


def test_purchase_invoice_reject_moves_back_to_revision_requested(db_session, project, person):
    vendor, installation_project, quote = _setup_vendor_project_quote(db_session, project)
    _submit_quote(db_session, str(vendor.id), str(quote.id), quote.id)
    invoice = vendor_service.vendor_purchase_invoices.create(
        db_session,
        VendorPurchaseInvoiceCreate(project_id=installation_project.id),
        vendor_id=str(vendor.id),
        created_by_person_id=None,
    )
    vendor_service.vendor_purchase_invoice_line_items.create(
        db_session,
        VendorPurchaseInvoiceLineItemCreate(
            invoice_id=invoice.id,
            item_type="labor",
            description="Splicing work",
            quantity=Decimal("1.000"),
            unit_price=Decimal("175.00"),
        ),
        vendor_id=str(vendor.id),
    )
    vendor_service.vendor_purchase_invoices.submit(db_session, str(invoice.id), vendor_id=str(vendor.id))

    rejected = vendor_service.vendor_purchase_invoices.reject(
        db_session,
        invoice_id=str(invoice.id),
        reviewer_person_id=str(person.id),
        review_notes="Please correct amount",
    )

    assert rejected.status.value == "revision_requested"
    assert rejected.review_notes == "Please correct amount"
