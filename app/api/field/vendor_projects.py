from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.vendor import (
    AsBuiltRouteCreate,
    AsBuiltRouteRead,
    FieldProjectSite,
    InstallationProjectRead,
    VendorProjectLifecycle,
    VendorProjectListItem,
)
from app.services.field.vendor_projects import field_vendor_projects
from app.services.vendor_auth_tokens import require_vendor_token

router = APIRouter(tags=["field-vendor-projects"])


@router.get("/projects", response_model=ListResponse[VendorProjectListItem])
def list_vendor_projects(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    vendor=Depends(require_vendor_token),
    db: Session = Depends(get_db),
):
    items = field_vendor_projects.list_mine_detailed(db, vendor["vendor_id"], limit=limit, offset=offset)
    return {"items": items, "count": len(items), "limit": limit, "offset": offset}


@router.get("/projects/{project_id}")
def get_vendor_project(
    project_id: str,
    vendor=Depends(require_vendor_token),
    db: Session = Depends(get_db),
):
    bundle = field_vendor_projects.get_detail(db, vendor["vendor_id"], project_id)
    return {
        "project": InstallationProjectRead.model_validate(bundle["project"]),
        "site": FieldProjectSite.model_validate(bundle["site"]) if bundle["site"] else None,
        "lifecycle": (VendorProjectLifecycle.model_validate(bundle["lifecycle"]) if bundle["lifecycle"] else None),
        "submissions": [AsBuiltRouteRead.model_validate(s) for s in bundle["submissions"]],
        "rejected_for_resubmission": (
            AsBuiltRouteRead.model_validate(bundle["rejected_for_resubmission"])
            if bundle["rejected_for_resubmission"]
            else None
        ),
        "attachment_count": len(bundle["attachments"]),
    }


@router.post(
    "/projects/{project_id}/as-built",
    response_model=AsBuiltRouteRead,
    status_code=status.HTTP_201_CREATED,
)
def submit_vendor_as_built(
    project_id: str,
    payload: AsBuiltRouteCreate,
    vendor=Depends(require_vendor_token),
    db: Session = Depends(get_db),
):
    return field_vendor_projects.submit_as_built(
        db,
        vendor["vendor_id"],
        vendor["person_id"],
        project_id,
        payload,
    )


@router.get("/projects/{project_id}/as-built/{route_id}/report")
def download_vendor_as_built_report(
    project_id: str,
    route_id: str,
    vendor=Depends(require_vendor_token),
    db: Session = Depends(get_db),
):
    """Download the generated as-built PDF for a route the vendor owns."""
    route = field_vendor_projects.get_as_built_report(db, vendor["vendor_id"], project_id, route_id)
    # get_as_built_report guarantees a generated, on-disk report (404s otherwise).
    return FileResponse(
        str(route.report_file_path),
        media_type="application/pdf",
        filename=route.report_file_name or f"as_built_{route.id}.pdf",
    )
