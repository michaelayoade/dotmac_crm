from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.vendor import (
    AsBuiltRouteCreate,
    AsBuiltRouteRead,
    InstallationProjectRead,
    ProjectQuoteCreate,
    ProjectQuoteRead,
    ProposedRouteRevisionCreate,
    ProposedRouteRevisionCreateRequest,
    ProposedRouteRevisionRead,
    QuoteLineItemCreate,
    QuoteLineItemCreateRequest,
    QuoteLineItemRead,
    QuoteLineItemUpdate,
)
from app.services import vendor as vendor_service
from app.services import vendor_portal
from app.services.response import list_response

router = APIRouter(prefix="/vendor", tags=["vendor-portal"])


def require_vendor_context(request: Request, db: Session):
    context = vendor_portal.get_context(db, request.cookies.get(vendor_portal.SESSION_COOKIE_NAME))
    if not context:
        raise HTTPException(status_code=401, detail="Vendor session required")
    return context


@router.get(
    "/projects/available",
    response_model=ListResponse[InstallationProjectRead],
)
def available_projects(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    context = require_vendor_context(request, db)
    items = vendor_service.installation_projects.list_available_for_vendor(db, str(context["vendor"].id), limit, offset)
    return list_response(items, limit, offset)


@router.get(
    "/projects/mine",
    response_model=ListResponse[InstallationProjectRead],
)
def my_projects(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    context = require_vendor_context(request, db)
    items = vendor_service.installation_projects.list_for_vendor(db, str(context["vendor"].id), limit, offset)
    return list_response(items, limit, offset)


@router.post(
    "/quotes",
    response_model=ProjectQuoteRead,
    status_code=status.HTTP_201_CREATED,
)
def create_quote(
    request: Request,
    payload: ProjectQuoteCreate,
    db: Session = Depends(get_db),
):
    context = require_vendor_context(request, db)
    return vendor_service.project_quotes.create(
        db,
        payload,
        vendor_id=str(context["vendor"].id),
        created_by_person_id=str(context["person"].id),
    )


@router.post(
    "/quotes/{quote_id}/line-items",
    response_model=QuoteLineItemRead,
    status_code=status.HTTP_201_CREATED,
)
def add_line_item(
    request: Request,
    quote_id: str,
    payload: QuoteLineItemCreateRequest,
    db: Session = Depends(get_db),
):
    context = require_vendor_context(request, db)
    payload_data = payload.model_dump()
    payload_data["quote_id"] = quote_id
    return vendor_service.quote_line_items.create(
        db,
        QuoteLineItemCreate(**payload_data),
        vendor_id=str(context["vendor"].id),
    )


@router.get(
    "/quotes/{quote_id}/line-items",
    response_model=ListResponse[QuoteLineItemRead],
)
def list_line_items(
    request: Request,
    quote_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    context = require_vendor_context(request, db)
    quote = vendor_service.project_quotes.get(db, quote_id)
    if str(quote.vendor_id) != str(context["vendor"].id):
        raise HTTPException(status_code=403, detail="Quote ownership required")
    items = vendor_service.quote_line_items.list(
        db,
        quote_id=quote_id,
        is_active=None,
        order_by="created_at",
        order_dir="asc",
        limit=limit,
        offset=offset,
    )
    return list_response(items, limit, offset)


@router.patch(
    "/quotes/{quote_id}/line-items/{line_item_id}",
    response_model=QuoteLineItemRead,
)
def update_line_item(
    request: Request,
    quote_id: str,
    line_item_id: str,
    payload: QuoteLineItemUpdate,
    db: Session = Depends(get_db),
):
    context = require_vendor_context(request, db)
    return vendor_service.quote_line_items.update(
        db,
        quote_id=quote_id,
        line_item_id=line_item_id,
        payload=payload,
        vendor_id=str(context["vendor"].id),
    )


@router.delete(
    "/quotes/{quote_id}/line-items/{line_item_id}",
    response_model=QuoteLineItemRead,
)
def delete_line_item(
    request: Request,
    quote_id: str,
    line_item_id: str,
    db: Session = Depends(get_db),
):
    context = require_vendor_context(request, db)
    return vendor_service.quote_line_items.delete(
        db,
        quote_id=quote_id,
        line_item_id=line_item_id,
        vendor_id=str(context["vendor"].id),
    )


@router.post(
    "/quotes/{quote_id}/route-revisions",
    response_model=ProposedRouteRevisionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_route_revision(
    request: Request,
    quote_id: str,
    payload: ProposedRouteRevisionCreateRequest,
    db: Session = Depends(get_db),
):
    context = require_vendor_context(request, db)
    payload_data = payload.model_dump()
    payload_data["quote_id"] = quote_id
    return vendor_service.proposed_route_revisions.create(
        db,
        ProposedRouteRevisionCreate(**payload_data),
        vendor_id=str(context["vendor"].id),
    )


@router.post(
    "/quotes/{quote_id}/route-revisions/{revision_id}/submit",
    response_model=ProposedRouteRevisionRead,
)
def submit_route_revision(
    request: Request,
    quote_id: str,
    revision_id: str,
    db: Session = Depends(get_db),
):
    context = require_vendor_context(request, db)
    return vendor_service.proposed_route_revisions.submit(
        db,
        revision_id,
        person_id=str(context["person"].id),
        vendor_id=str(context["vendor"].id),
    )


@router.post(
    "/quotes/{quote_id}/submit",
    response_model=ProjectQuoteRead,
)
def submit_quote(
    request: Request,
    quote_id: str,
    db: Session = Depends(get_db),
):
    context = require_vendor_context(request, db)
    return vendor_service.project_quotes.submit(db, quote_id, vendor_id=str(context["vendor"].id))


@router.post(
    "/as-built",
    response_model=AsBuiltRouteRead,
    status_code=status.HTTP_201_CREATED,
)
def submit_as_built(
    request: Request,
    payload: AsBuiltRouteCreate,
    db: Session = Depends(get_db),
):
    context = require_vendor_context(request, db)
    project = vendor_service.installation_projects.get(db, str(payload.project_id))
    vendor_id = str(context["vendor"].id)
    if not vendor_service.project_quotes.has_submitted_for_vendor_project(db, str(project.id), vendor_id):
        raise HTTPException(status_code=403, detail="Quote must be submitted before as-built can be provided")
    return vendor_service.as_built_routes.create(
        db,
        payload,
        vendor_id=vendor_id,
        submitted_by_person_id=str(context["person"].id),
    )
