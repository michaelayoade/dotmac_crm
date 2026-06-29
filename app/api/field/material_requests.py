from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.field import FieldMaterialRequestCreate
from app.schemas.material_request import MaterialRequestRead
from app.services.auth_dependencies import require_user_auth
from app.services.field.material_requests import field_material_requests
from app.services.response import list_response

router = APIRouter(prefix="/material-requests", tags=["field-material-requests"])


@router.get("", response_model=ListResponse[MaterialRequestRead])
def list_field_material_requests(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    items = field_material_requests.list_mine(
        db,
        auth["person_id"],
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    return list_response(items, limit, offset)


@router.post("", response_model=MaterialRequestRead, status_code=status.HTTP_201_CREATED)
def create_field_material_request(
    payload: FieldMaterialRequestCreate,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return field_material_requests.create(db, auth["person_id"], payload)


@router.get("/{material_request_id}", response_model=MaterialRequestRead)
def get_field_material_request(
    material_request_id: str,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return field_material_requests.get_mine(db, auth["person_id"], material_request_id)


@router.post("/{material_request_id}/submit", response_model=MaterialRequestRead)
def submit_field_material_request(
    material_request_id: str,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return field_material_requests.submit(db, auth["person_id"], material_request_id)
