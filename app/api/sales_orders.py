from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.sales_order import (
    SalesOrderCreate,
    SalesOrderLineCreate,
    SalesOrderLineRead,
    SalesOrderLineUpdate,
    SalesOrderRead,
    SalesOrderUpdate,
)
from app.services import sales_orders as sales_order_service

router = APIRouter(prefix="/sales-orders", tags=["sales-orders"])


@router.post("", response_model=SalesOrderRead, status_code=status.HTTP_201_CREATED)
def create_sales_order(payload: SalesOrderCreate, db: Session = Depends(get_db)):
    return sales_order_service.sales_orders.create(db, payload)


@router.get("/{sales_order_id}", response_model=SalesOrderRead)
def get_sales_order(sales_order_id: str, db: Session = Depends(get_db)):
    return sales_order_service.sales_orders.get(db, sales_order_id)


@router.get("", response_model=ListResponse[SalesOrderRead])
def list_sales_orders(
    person_id: str | None = None,
    account_id: str | None = None,
    quote_id: str | None = None,
    status: str | None = None,
    payment_status: str | None = None,
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return sales_order_service.sales_orders.list_response(
        db,
        person_id,
        account_id,
        quote_id,
        status,
        payment_status,
        is_active,
        order_by,
        order_dir,
        limit,
        offset,
    )


@router.patch("/{sales_order_id}", response_model=SalesOrderRead)
def update_sales_order(sales_order_id: str, payload: SalesOrderUpdate, db: Session = Depends(get_db)):
    return sales_order_service.sales_orders.update(db, sales_order_id, payload)


@router.delete("/{sales_order_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sales_order(sales_order_id: str, db: Session = Depends(get_db)):
    sales_order_service.sales_orders.delete(db, sales_order_id)


@router.post(
    "/{sales_order_id}/lines",
    response_model=SalesOrderLineRead,
    status_code=status.HTTP_201_CREATED,
)
def create_sales_order_line(sales_order_id: str, payload: SalesOrderLineCreate, db: Session = Depends(get_db)):
    data = payload.model_copy(update={"sales_order_id": sales_order_id})
    return sales_order_service.sales_order_lines.create(db, data)


@router.get("/{sales_order_id}/lines", response_model=ListResponse[SalesOrderLineRead])
def list_sales_order_lines(
    sales_order_id: str,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="asc", pattern="^(asc|desc)$"),
    limit: int = Query(default=200, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return sales_order_service.sales_order_lines.list_response(db, sales_order_id, order_by, order_dir, limit, offset)


@router.patch("/lines/{line_id}", response_model=SalesOrderLineRead)
def update_sales_order_line(line_id: str, payload: SalesOrderLineUpdate, db: Session = Depends(get_db)):
    return sales_order_service.sales_order_lines.update(db, line_id, payload)
