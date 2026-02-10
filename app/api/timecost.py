from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.timecost import (
    BillingRateCreate,
    BillingRateRead,
    BillingRateUpdate,
    CostRateCreate,
    CostRateRead,
    CostRateUpdate,
    CostSummary,
    ExpenseLineCreate,
    ExpenseLineRead,
    ExpenseLineUpdate,
    WorkLogCreate,
    WorkLogRead,
    WorkLogUpdate,
)
from app.services import timecost as timecost_service
from app.services.response import list_response

router = APIRouter(prefix="/timecost", tags=["timecost"])


@router.post("/work-logs", response_model=WorkLogRead, status_code=status.HTTP_201_CREATED)
def create_work_log(payload: WorkLogCreate, db: Session = Depends(get_db)):
    return timecost_service.work_logs.create(db, payload)


@router.get("/work-logs/{log_id}", response_model=WorkLogRead)
def get_work_log(log_id: str, db: Session = Depends(get_db)):
    return timecost_service.work_logs.get(db, log_id)


@router.get("/work-logs", response_model=ListResponse[WorkLogRead])
def list_work_logs(
    work_order_id: str | None = None,
    person_id: str | None = None,
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = timecost_service.work_logs.list(
        db, work_order_id, person_id, is_active, order_by, order_dir, limit, offset
    )
    return list_response(items, limit, offset)


@router.patch("/work-logs/{log_id}", response_model=WorkLogRead)
def update_work_log(log_id: str, payload: WorkLogUpdate, db: Session = Depends(get_db)):
    return timecost_service.work_logs.update(db, log_id, payload)


@router.delete("/work-logs/{log_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_work_log(log_id: str, db: Session = Depends(get_db)):
    timecost_service.work_logs.delete(db, log_id)


@router.post("/expenses", response_model=ExpenseLineRead, status_code=status.HTTP_201_CREATED)
def create_expense(payload: ExpenseLineCreate, db: Session = Depends(get_db)):
    return timecost_service.expense_lines.create(db, payload)


@router.get("/expenses/{expense_id}", response_model=ExpenseLineRead)
def get_expense(expense_id: str, db: Session = Depends(get_db)):
    return timecost_service.expense_lines.get(db, expense_id)


@router.get("/expenses", response_model=ListResponse[ExpenseLineRead])
def list_expenses(
    work_order_id: str | None = None,
    project_id: str | None = None,
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = timecost_service.expense_lines.list(
        db, work_order_id, project_id, is_active, order_by, order_dir, limit, offset
    )
    return list_response(items, limit, offset)


@router.patch("/expenses/{expense_id}", response_model=ExpenseLineRead)
def update_expense(
    expense_id: str, payload: ExpenseLineUpdate, db: Session = Depends(get_db)
):
    return timecost_service.expense_lines.update(db, expense_id, payload)


@router.delete("/expenses/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_expense(expense_id: str, db: Session = Depends(get_db)):
    timecost_service.expense_lines.delete(db, expense_id)


@router.post("/cost-rates", response_model=CostRateRead, status_code=status.HTTP_201_CREATED)
def create_cost_rate(payload: CostRateCreate, db: Session = Depends(get_db)):
    return timecost_service.cost_rates.create(db, payload)


@router.get("/cost-rates/{rate_id}", response_model=CostRateRead)
def get_cost_rate(rate_id: str, db: Session = Depends(get_db)):
    return timecost_service.cost_rates.get(db, rate_id)


@router.get("/cost-rates", response_model=ListResponse[CostRateRead])
def list_cost_rates(
    person_id: str | None = None,
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = timecost_service.cost_rates.list(
        db, person_id, is_active, order_by, order_dir, limit, offset
    )
    return list_response(items, limit, offset)


@router.patch("/cost-rates/{rate_id}", response_model=CostRateRead)
def update_cost_rate(
    rate_id: str, payload: CostRateUpdate, db: Session = Depends(get_db)
):
    return timecost_service.cost_rates.update(db, rate_id, payload)


@router.delete("/cost-rates/{rate_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cost_rate(rate_id: str, db: Session = Depends(get_db)):
    timecost_service.cost_rates.delete(db, rate_id)


@router.post(
    "/billing-rates", response_model=BillingRateRead, status_code=status.HTTP_201_CREATED
)
def create_billing_rate(payload: BillingRateCreate, db: Session = Depends(get_db)):
    return timecost_service.billing_rates.create(db, payload)


@router.get("/billing-rates/{rate_id}", response_model=BillingRateRead)
def get_billing_rate(rate_id: str, db: Session = Depends(get_db)):
    return timecost_service.billing_rates.get(db, rate_id)


@router.get("/billing-rates", response_model=ListResponse[BillingRateRead])
def list_billing_rates(
    is_active: bool | None = None,
    order_by: str = Query(default="created_at"),
    order_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    items = timecost_service.billing_rates.list(
        db, is_active, order_by, order_dir, limit, offset
    )
    return list_response(items, limit, offset)


@router.patch("/billing-rates/{rate_id}", response_model=BillingRateRead)
def update_billing_rate(
    rate_id: str, payload: BillingRateUpdate, db: Session = Depends(get_db)
):
    return timecost_service.billing_rates.update(db, rate_id, payload)


@router.delete("/billing-rates/{rate_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_billing_rate(rate_id: str, db: Session = Depends(get_db)):
    timecost_service.billing_rates.delete(db, rate_id)


@router.get("/work-orders/{work_order_id}/cost-summary", response_model=CostSummary)
def work_order_cost_summary(work_order_id: str, db: Session = Depends(get_db)):
    return timecost_service.work_order_cost_summary(db, work_order_id)


@router.get("/projects/{project_id}/cost-summary", response_model=CostSummary)
def project_cost_summary(project_id: str, db: Session = Depends(get_db)):
    return timecost_service.project_cost_summary(db, project_id)
