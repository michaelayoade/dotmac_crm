from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.field import (
    FieldCustomerSearchItem,
    FieldSalesOrderCreate,
    FieldSalesOrderRead,
)
from app.services import customer_search as customer_search_service
from app.services.auth_dependencies import require_user_auth
from app.services.field.sales_orders import field_sales_orders
from app.services.response import list_response

router = APIRouter(prefix="/sales-orders", tags=["field-sales-orders"])


@router.get("/customers/search", response_model=ListResponse[FieldCustomerSearchItem])
def search_field_sales_customers(
    q: str = Query(min_length=1),
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    items = [item for item in customer_search_service.search(db, q, limit=limit) if item.get("type") == "person"]
    return list_response(items, limit, 0)


@router.get("", response_model=ListResponse[FieldSalesOrderRead])
def list_field_sales_orders(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    orders = field_sales_orders.list_mine(
        db,
        auth["person_id"],
        limit=limit,
        offset=offset,
    )
    return list_response([FieldSalesOrderRead.from_order(order) for order in orders], limit, offset)


@router.post("", response_model=FieldSalesOrderRead, status_code=status.HTTP_201_CREATED)
def create_field_sales_order(
    payload: FieldSalesOrderCreate,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    order = field_sales_orders.create(db, auth["person_id"], payload)
    return FieldSalesOrderRead.from_order(order)
