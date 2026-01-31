from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.vendor import (
    AsBuiltCompareResponse,
    AsBuiltRouteRead,
    InstallationProjectCreate,
    InstallationProjectRead,
    ProjectBidOpenRequest,
    ProjectQuoteRead,
    QuoteApprovalRequest,
    QuoteRejectRequest,
    VendorCreate,
    VendorRead,
    VendorUpdate,
)
from app.services import vendor as vendor_service

router = APIRouter(prefix="/vendors", tags=["vendors-admin"])


@router.post("", response_model=VendorRead, status_code=status.HTTP_201_CREATED)
def create_vendor(payload: VendorCreate, db: Session = Depends(get_db)):
    return vendor_service.vendors.create(db, payload)


@router.get("/{vendor_id}", response_model=VendorRead)
def get_vendor(vendor_id: str, db: Session = Depends(get_db)):
    return vendor_service.vendors.get(db, vendor_id)


@router.get("", response_model=ListResponse[VendorRead])
def list_vendors(
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return vendor_service.vendors.list_response(
        db, is_active, order_by, order_dir, limit, offset
    )


@router.patch("/{vendor_id}", response_model=VendorRead)
def update_vendor(vendor_id: str, payload: VendorUpdate, db: Session = Depends(get_db)):
    return vendor_service.vendors.update(db, vendor_id, payload)


@router.delete("/{vendor_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vendor(vendor_id: str, db: Session = Depends(get_db)):
    vendor_service.vendors.delete(db, vendor_id)


@router.post("/projects", response_model=InstallationProjectRead, status_code=status.HTTP_201_CREATED)
def create_installation_project(payload: InstallationProjectCreate, db: Session = Depends(get_db)):
    return vendor_service.installation_projects.create(db, payload)


@router.post("/projects/{project_id}/open-bidding", response_model=InstallationProjectRead)
def open_bidding(
    project_id: str,
    payload: ProjectBidOpenRequest,
    db: Session = Depends(get_db),
):
    return vendor_service.installation_projects.open_for_bidding(
        db, project_id, payload.bid_days
    )


@router.post("/projects/{project_id}/assign/{vendor_id}", response_model=InstallationProjectRead)
def assign_vendor(project_id: str, vendor_id: str, db: Session = Depends(get_db)):
    return vendor_service.installation_projects.assign_vendor(db, project_id, vendor_id)


@router.post("/quotes/{quote_id}/approve", response_model=ProjectQuoteRead)
def approve_quote(
    quote_id: str,
    payload: QuoteApprovalRequest,
    db: Session = Depends(get_db),
):
    return vendor_service.project_quotes.approve(
        db,
        quote_id,
        reviewer_person_id=str(payload.reviewer_person_id),
        review_notes=payload.review_notes,
        override=payload.override_threshold,
    )


@router.post("/quotes/{quote_id}/reject", response_model=ProjectQuoteRead)
def reject_quote(
    quote_id: str,
    payload: QuoteRejectRequest,
    db: Session = Depends(get_db),
):
    return vendor_service.project_quotes.reject(
        db,
        quote_id,
        reviewer_person_id=str(payload.reviewer_person_id),
        review_notes=payload.review_notes,
    )


@router.post("/as-built/{as_built_id}/accept", response_model=AsBuiltRouteRead)
def accept_as_built(as_built_id: str, reviewer_id: str, db: Session = Depends(get_db)):
    return vendor_service.as_built_routes.accept_and_convert(
        db, as_built_id, reviewer_id
    )


@router.get("/as-built/{as_built_id}/compare", response_model=AsBuiltCompareResponse)
def compare_as_built(as_built_id: str, db: Session = Depends(get_db)):
    result = vendor_service.as_built_routes.compare(db, as_built_id)
    return AsBuiltCompareResponse(**result)
