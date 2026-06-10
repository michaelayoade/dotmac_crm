from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.vendor import AsBuiltRouteCreate, AsBuiltRouteRead, InstallationProjectRead
from app.services.field.vendor_projects import field_vendor_projects
from app.services.vendor_auth_tokens import require_vendor_token

router = APIRouter(tags=["field-vendor-projects"])


@router.get("/projects", response_model=ListResponse[InstallationProjectRead])
def list_vendor_projects(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    vendor=Depends(require_vendor_token),
    db: Session = Depends(get_db),
):
    items = field_vendor_projects.list_mine(db, vendor["vendor_id"], limit=limit, offset=offset)
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
