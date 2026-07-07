from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.expense_request import ExpenseCategoryRead, ExpenseRequestRead
from app.schemas.field import FieldAttachmentRead, FieldExpenseRequestCreate
from app.schemas.typeahead import TypeaheadItem
from app.services import typeahead as typeahead_service
from app.services.auth_dependencies import require_user_auth
from app.services.field import field_attachments
from app.services.field.expense_requests import field_expense_requests
from app.services.response import list_response

router = APIRouter(prefix="/expense-requests", tags=["field-expense-requests"])


@router.get("", response_model=ListResponse[ExpenseRequestRead])
def list_field_expense_requests(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    items = field_expense_requests.list_mine(
        db,
        auth["person_id"],
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    return list_response(items, limit, offset)


@router.get("/categories", response_model=list[ExpenseCategoryRead])
def list_field_expense_categories(
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return field_expense_requests.list_categories(db)


@router.get("/vendors", response_model=ListResponse[TypeaheadItem])
def list_field_expense_vendors(
    q: str = Query(default=""),
    limit: int = Query(default=25, ge=1, le=50),
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    del auth
    return typeahead_service.vendors_response(db, q, limit)


@router.post(
    "/receipts",
    response_model=FieldAttachmentRead,
    status_code=status.HTTP_201_CREATED,
)
def upload_field_expense_receipt(
    file: UploadFile = File(...),
    work_order_id: str = Form(...),
    client_ref: str | None = Form(default=None),
    latitude: float | None = Form(default=None),
    longitude: float | None = Form(default=None),
    captured_at: str | None = Form(default=None),
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return field_attachments.create(
        db,
        kind="photo",
        file_name=file.filename or "receipt",
        mime_type=file.content_type or "",
        content=file.file.read(),
        client_ref=client_ref,
        work_order_id=work_order_id,
        latitude=latitude,
        longitude=longitude,
        captured_at=captured_at,
        uploaded_by_person_id=auth["person_id"],
    )


@router.post("", response_model=ExpenseRequestRead, status_code=status.HTTP_201_CREATED)
def create_field_expense_request(
    payload: FieldExpenseRequestCreate,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return field_expense_requests.create(db, auth["person_id"], payload)


@router.get("/{expense_request_id}", response_model=ExpenseRequestRead)
def get_field_expense_request(
    expense_request_id: str,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return field_expense_requests.get_mine(db, auth["person_id"], expense_request_id)


@router.post("/{expense_request_id}/cancel", response_model=ExpenseRequestRead)
def cancel_field_expense_request(
    expense_request_id: str,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return field_expense_requests.cancel(db, auth["person_id"], expense_request_id)
