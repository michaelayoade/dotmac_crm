"""Vendor crew quoting for the field app.

Lets a mobile crew bid on an installation project: open (or resume) a draft
quote, add/remove line items, and submit it for review — the same flow the
vendor web portal runs, wrapped with bearer-token caller scoping. All ownership
and state guards live in the underlying vendor service; this layer only fixes
the caller's vendor_id so a crew can never touch another vendor's quote.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.vendor import ProjectQuote, ProposedRouteRevision, QuoteLineItem
from app.schemas.vendor import (
    ProposedRouteRevisionCreate,
    QuoteLineItemCreate,
    QuoteLineItemCreateRequest,
)
from app.services import vendor as vendor_service
from app.services.common import coerce_uuid


def _scoped_quote(db: Session, vendor_id: str, quote_id: str) -> ProjectQuote:
    quote = db.get(ProjectQuote, coerce_uuid(quote_id))
    if not quote or not quote.is_active or str(quote.vendor_id) != str(coerce_uuid(vendor_id)):
        # Same 404 for missing and foreign quotes: existence must not leak.
        raise HTTPException(status_code=404, detail="Quote not found")
    return quote


class FieldVendorQuotes:
    @staticmethod
    def list_mine(
        db: Session,
        vendor_id: str,
        *,
        project_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ProjectQuote]:
        return vendor_service.project_quotes.list(
            db,
            project_id=project_id,
            vendor_id=str(vendor_id),
            status=status,
            is_active=True,
            order_by="created_at",
            order_dir="desc",
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def open_draft(db: Session, vendor_id: str, project_id: str, person_id: str | None) -> ProjectQuote:
        """Resume the vendor's draft for a project, or start one — the entry
        point for placing a bid from the field."""
        return vendor_service.project_quotes.get_or_create_for_vendor_project(
            db,
            installation_project_id=str(project_id),
            vendor_id=str(vendor_id),
            created_by_person_id=str(person_id) if person_id else None,
        )

    @staticmethod
    def get_detail(db: Session, vendor_id: str, quote_id: str) -> dict:
        quote = _scoped_quote(db, vendor_id, quote_id)
        line_items = vendor_service.quote_line_items.list(
            db,
            quote_id=str(quote.id),
            is_active=True,
            order_by="created_at",
            order_dir="asc",
            limit=200,
            offset=0,
        )
        return {"quote": quote, "line_items": line_items}

    @staticmethod
    def add_line_item(db: Session, vendor_id: str, quote_id: str, payload: QuoteLineItemCreateRequest) -> QuoteLineItem:
        quote = _scoped_quote(db, vendor_id, quote_id)
        create = QuoteLineItemCreate(quote_id=quote.id, **payload.model_dump())
        return vendor_service.quote_line_items.create(db, create, vendor_id=str(vendor_id))

    @staticmethod
    def remove_line_item(db: Session, vendor_id: str, quote_id: str, line_item_id: str) -> None:
        # _scoped_quote enforces vendor ownership before the service call.
        _scoped_quote(db, vendor_id, quote_id)
        vendor_service.quote_line_items.delete(db, str(quote_id), str(line_item_id), vendor_id=str(vendor_id))

    @staticmethod
    def submit(db: Session, vendor_id: str, quote_id: str) -> ProjectQuote:
        _scoped_quote(db, vendor_id, quote_id)
        return vendor_service.project_quotes.submit(db, str(quote_id), str(vendor_id))

    @staticmethod
    def list_proposed_routes(db: Session, vendor_id: str, quote_id: str) -> list[ProposedRouteRevision]:
        _scoped_quote(db, vendor_id, quote_id)
        return vendor_service.proposed_route_revisions.list(
            db,
            quote_id=str(quote_id),
            status=None,
            order_by="revision_number",
            order_dir="asc",
            limit=100,
            offset=0,
        )

    @staticmethod
    def add_proposed_route(
        db: Session,
        vendor_id: str,
        quote_id: str,
        person_id: str | None,
        *,
        geojson: dict,
        length_meters: float | None = None,
        submit: bool = True,
    ) -> ProposedRouteRevision:
        """Attach a proposed route (drawn/walked on the map) to the quote — the
        map half of a complete bid. Created as a new revision and, by default,
        submitted for review in the same call so the crew has one action."""
        quote = _scoped_quote(db, vendor_id, quote_id)
        revision = vendor_service.proposed_route_revisions.create(
            db,
            payload=ProposedRouteRevisionCreate(
                quote_id=quote.id,
                geojson=geojson,
                length_meters=length_meters,
            ),
            vendor_id=str(vendor_id),
        )
        if submit:
            revision = vendor_service.proposed_route_revisions.submit(
                db, str(revision.id), str(person_id) if person_id else None, vendor_id=str(vendor_id)
            )
        return revision


field_vendor_quotes = FieldVendorQuotes()
