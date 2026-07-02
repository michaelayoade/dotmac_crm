from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.vendor import (
    ProjectQuoteRead,
    ProposedRouteRevisionCreateRequest,
    ProposedRouteRevisionRead,
    QuoteLineItemCreateRequest,
    QuoteLineItemRead,
)
from app.services.field.vendor_quotes import field_vendor_quotes
from app.services.vendor_auth_tokens import require_vendor_token

router = APIRouter(tags=["field-vendor-quotes"])


@router.get("/quotes", response_model=ListResponse[ProjectQuoteRead])
def list_vendor_quotes(
    project_id: str | None = Query(default=None),
    quote_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    vendor=Depends(require_vendor_token),
    db: Session = Depends(get_db),
):
    items = field_vendor_quotes.list_mine(
        db, vendor["vendor_id"], project_id=project_id, status=quote_status, limit=limit, offset=offset
    )
    return {"items": items, "count": len(items), "limit": limit, "offset": offset}


@router.post("/projects/{project_id}/quote", response_model=ProjectQuoteRead)
def open_vendor_quote_draft(
    project_id: str,
    vendor=Depends(require_vendor_token),
    db: Session = Depends(get_db),
):
    """Resume (or start) this vendor's draft quote for the project — the bid entry point."""
    return field_vendor_quotes.open_draft(db, vendor["vendor_id"], project_id, vendor["person_id"])


@router.get("/quotes/{quote_id}")
def get_vendor_quote(
    quote_id: str,
    vendor=Depends(require_vendor_token),
    db: Session = Depends(get_db),
):
    bundle = field_vendor_quotes.get_detail(db, vendor["vendor_id"], quote_id)
    return {
        "quote": ProjectQuoteRead.model_validate(bundle["quote"]),
        "line_items": [QuoteLineItemRead.model_validate(i) for i in bundle["line_items"]],
    }


@router.post(
    "/quotes/{quote_id}/line-items",
    response_model=QuoteLineItemRead,
    status_code=status.HTTP_201_CREATED,
)
def add_vendor_quote_line_item(
    quote_id: str,
    payload: QuoteLineItemCreateRequest,
    vendor=Depends(require_vendor_token),
    db: Session = Depends(get_db),
):
    return field_vendor_quotes.add_line_item(db, vendor["vendor_id"], quote_id, payload)


@router.delete("/quotes/{quote_id}/line-items/{line_item_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_vendor_quote_line_item(
    quote_id: str,
    line_item_id: str,
    vendor=Depends(require_vendor_token),
    db: Session = Depends(get_db),
):
    field_vendor_quotes.remove_line_item(db, vendor["vendor_id"], quote_id, line_item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/quotes/{quote_id}/submit", response_model=ProjectQuoteRead)
def submit_vendor_quote(
    quote_id: str,
    vendor=Depends(require_vendor_token),
    db: Session = Depends(get_db),
):
    return field_vendor_quotes.submit(db, vendor["vendor_id"], quote_id)


@router.get("/quotes/{quote_id}/proposed-routes", response_model=ListResponse[ProposedRouteRevisionRead])
def list_vendor_proposed_routes(
    quote_id: str,
    vendor=Depends(require_vendor_token),
    db: Session = Depends(get_db),
):
    items = field_vendor_quotes.list_proposed_routes(db, vendor["vendor_id"], quote_id)
    return {"items": items, "count": len(items), "limit": len(items), "offset": 0}


@router.post(
    "/quotes/{quote_id}/proposed-route",
    response_model=ProposedRouteRevisionRead,
    status_code=status.HTTP_201_CREATED,
)
def add_vendor_proposed_route(
    quote_id: str,
    payload: ProposedRouteRevisionCreateRequest,
    submit: bool = Query(default=True, description="Submit the revision for review in the same call."),
    vendor=Depends(require_vendor_token),
    db: Session = Depends(get_db),
):
    """Attach a proposed route (the map half of a complete bid) to the quote."""
    return field_vendor_quotes.add_proposed_route(
        db,
        vendor["vendor_id"],
        quote_id,
        vendor["person_id"],
        geojson=payload.geojson,
        length_meters=payload.length_meters,
        submit=submit,
    )
