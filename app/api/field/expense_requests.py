from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.expense_request import ExpenseCategoryRead, ExpenseRequestRead
from app.schemas.field import FieldExpenseRequestCreate
from app.services.auth_dependencies import require_user_auth
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
