from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.person import Person
from app.models.projects import Project
from app.models.timecost import BillingRate, CostRate, ExpenseLine, WorkLog
from app.models.workforce import WorkOrder
from app.schemas.timecost import (
    BillingRateCreate,
    BillingRateUpdate,
    CostRateCreate,
    CostRateUpdate,
    ExpenseLineCreate,
    ExpenseLineUpdate,
    WorkLogCreate,
    WorkLogUpdate,
)
from app.services import settings_spec
from app.services.common import apply_ordering, apply_pagination, coerce_uuid
from app.services.response import ListResponseMixin


def _ensure_person(db: Session, person_id: str):
    if not db.get(Person, coerce_uuid(person_id)):
        raise HTTPException(status_code=404, detail="Person not found")


def _ensure_work_order(db: Session, work_order_id: str):
    if not db.get(WorkOrder, coerce_uuid(work_order_id)):
        raise HTTPException(status_code=404, detail="Work order not found")


def _ensure_project(db: Session, project_id: str):
    if not db.get(Project, coerce_uuid(project_id)):
        raise HTTPException(status_code=404, detail="Project not found")


def _compute_minutes(start_at, end_at):
    if not start_at or not end_at:
        return 0
    delta = end_at - start_at
    return max(int(delta.total_seconds() // 60), 0)


class WorkLogs(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: WorkLogCreate):
        _ensure_work_order(db, str(payload.work_order_id))
        _ensure_person(db, str(payload.person_id))
        data = payload.model_dump()
        if data.get("minutes", 0) == 0 and data.get("end_at"):
            data["minutes"] = _compute_minutes(data["start_at"], data["end_at"])
        log = WorkLog(**data)
        db.add(log)
        db.commit()
        db.refresh(log)
        return log

    @staticmethod
    def get(db: Session, log_id: str):
        log = db.get(WorkLog, log_id)
        if not log:
            raise HTTPException(status_code=404, detail="Work log not found")
        return log

    @staticmethod
    def list(
        db: Session,
        work_order_id: str | None,
        person_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(WorkLog)
        if work_order_id:
            query = query.filter(WorkLog.work_order_id == work_order_id)
        if person_id:
            query = query.filter(WorkLog.person_id == person_id)
        if is_active is None:
            query = query.filter(WorkLog.is_active.is_(True))
        else:
            query = query.filter(WorkLog.is_active == is_active)
        query = apply_ordering(query, order_by, order_dir, {"created_at": WorkLog.created_at})
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, log_id: str, payload: WorkLogUpdate):
        log = db.get(WorkLog, log_id)
        if not log:
            raise HTTPException(status_code=404, detail="Work log not found")
        data = payload.model_dump(exclude_unset=True)
        if "start_at" in data or "end_at" in data:
            start_at = data.get("start_at", log.start_at)
            end_at = data.get("end_at", log.end_at)
            if data.get("minutes", 0) == 0 and end_at:
                data["minutes"] = _compute_minutes(start_at, end_at)
        for key, value in data.items():
            setattr(log, key, value)
        db.commit()
        db.refresh(log)
        return log

    @staticmethod
    def delete(db: Session, log_id: str):
        log = db.get(WorkLog, log_id)
        if not log:
            raise HTTPException(status_code=404, detail="Work log not found")
        log.is_active = False
        db.commit()


class ExpenseLines(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: ExpenseLineCreate):
        if payload.work_order_id:
            _ensure_work_order(db, str(payload.work_order_id))
        if payload.project_id:
            _ensure_project(db, str(payload.project_id))
        data = payload.model_dump()
        fields_set = payload.model_fields_set
        if "currency" not in fields_set:
            default_currency = settings_spec.resolve_value(db, SettingDomain.billing, "default_currency")
            if default_currency:
                data["currency"] = default_currency
        line = ExpenseLine(**data)
        db.add(line)
        db.commit()
        db.refresh(line)
        return line

    @staticmethod
    def get(db: Session, line_id: str):
        line = db.get(ExpenseLine, line_id)
        if not line:
            raise HTTPException(status_code=404, detail="Expense line not found")
        return line

    @staticmethod
    def list(
        db: Session,
        work_order_id: str | None,
        project_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(ExpenseLine)
        if work_order_id:
            query = query.filter(ExpenseLine.work_order_id == work_order_id)
        if project_id:
            query = query.filter(ExpenseLine.project_id == project_id)
        if is_active is None:
            query = query.filter(ExpenseLine.is_active.is_(True))
        else:
            query = query.filter(ExpenseLine.is_active == is_active)
        query = apply_ordering(query, order_by, order_dir, {"created_at": ExpenseLine.created_at})
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, line_id: str, payload: ExpenseLineUpdate):
        line = db.get(ExpenseLine, line_id)
        if not line:
            raise HTTPException(status_code=404, detail="Expense line not found")
        data = payload.model_dump(exclude_unset=True)
        if data.get("work_order_id"):
            _ensure_work_order(db, str(data["work_order_id"]))
        if data.get("project_id"):
            _ensure_project(db, str(data["project_id"]))
        for key, value in data.items():
            setattr(line, key, value)
        db.commit()
        db.refresh(line)
        return line

    @staticmethod
    def delete(db: Session, line_id: str):
        line = db.get(ExpenseLine, line_id)
        if not line:
            raise HTTPException(status_code=404, detail="Expense line not found")
        line.is_active = False
        db.commit()


class CostRates(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: CostRateCreate):
        if payload.person_id:
            _ensure_person(db, str(payload.person_id))
        data = payload.model_dump()
        rate = CostRate(**data)
        db.add(rate)
        db.commit()
        db.refresh(rate)
        return rate

    @staticmethod
    def get(db: Session, rate_id: str):
        rate = db.get(CostRate, rate_id)
        if not rate:
            raise HTTPException(status_code=404, detail="Cost rate not found")
        return rate

    @staticmethod
    def list(
        db: Session,
        person_id: str | None,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(CostRate)
        if person_id:
            query = query.filter(CostRate.person_id == person_id)
        if is_active is None:
            query = query.filter(CostRate.is_active.is_(True))
        else:
            query = query.filter(CostRate.is_active == is_active)
        query = apply_ordering(query, order_by, order_dir, {"created_at": CostRate.created_at})
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, rate_id: str, payload: CostRateUpdate):
        rate = db.get(CostRate, rate_id)
        if not rate:
            raise HTTPException(status_code=404, detail="Cost rate not found")
        data = payload.model_dump(exclude_unset=True)
        if data.get("person_id"):
            _ensure_person(db, str(data["person_id"]))
        for key, value in data.items():
            setattr(rate, key, value)
        db.commit()
        db.refresh(rate)
        return rate

    @staticmethod
    def delete(db: Session, rate_id: str):
        rate = db.get(CostRate, rate_id)
        if not rate:
            raise HTTPException(status_code=404, detail="Cost rate not found")
        rate.is_active = False
        db.commit()


class BillingRates(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: BillingRateCreate):
        data = payload.model_dump()
        fields_set = payload.model_fields_set
        if "currency" not in fields_set:
            default_currency = settings_spec.resolve_value(db, SettingDomain.billing, "default_currency")
            if default_currency:
                data["currency"] = default_currency
        rate = BillingRate(**data)
        db.add(rate)
        db.commit()
        db.refresh(rate)
        return rate

    @staticmethod
    def get(db: Session, rate_id: str):
        rate = db.get(BillingRate, rate_id)
        if not rate:
            raise HTTPException(status_code=404, detail="Billing rate not found")
        return rate

    @staticmethod
    def list(
        db: Session,
        is_active: bool | None,
        order_by: str,
        order_dir: str,
        limit: int,
        offset: int,
    ):
        query = db.query(BillingRate)
        if is_active is None:
            query = query.filter(BillingRate.is_active.is_(True))
        else:
            query = query.filter(BillingRate.is_active == is_active)
        query = apply_ordering(query, order_by, order_dir, {"created_at": BillingRate.created_at})
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def update(db: Session, rate_id: str, payload: BillingRateUpdate):
        rate = db.get(BillingRate, rate_id)
        if not rate:
            raise HTTPException(status_code=404, detail="Billing rate not found")
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(rate, key, value)
        db.commit()
        db.refresh(rate)
        return rate

    @staticmethod
    def delete(db: Session, rate_id: str):
        rate = db.get(BillingRate, rate_id)
        if not rate:
            raise HTTPException(status_code=404, detail="Billing rate not found")
        rate.is_active = False
        db.commit()


def _resolve_rate(db: Session, person_id: str, at_time) -> Decimal:
    person_uuid = coerce_uuid(person_id)
    rate = (
        db.query(CostRate)
        .filter(CostRate.person_id == person_uuid)
        .filter(CostRate.is_active.is_(True))
        .filter((CostRate.effective_from.is_(None)) | (CostRate.effective_from <= at_time))
        .filter((CostRate.effective_to.is_(None)) | (CostRate.effective_to >= at_time))
        .order_by(CostRate.effective_from.desc().nullslast())
        .first()
    )
    if not rate:
        return Decimal("0.00")
    return rate.hourly_rate


def work_order_cost_summary(db: Session, work_order_id: str) -> dict:
    work_order_uuid = coerce_uuid(work_order_id)
    _ensure_work_order(db, work_order_id)
    logs = db.query(WorkLog).filter(WorkLog.work_order_id == work_order_uuid).filter(WorkLog.is_active.is_(True)).all()
    labor = Decimal("0.00")
    for log in logs:
        rate = log.hourly_rate or _resolve_rate(db, str(log.person_id), log.start_at)
        labor += (Decimal(log.minutes) / Decimal("60.0")) * rate
    expenses = (
        db.query(ExpenseLine)
        .filter(ExpenseLine.work_order_id == work_order_uuid)
        .filter(ExpenseLine.is_active.is_(True))
        .all()
    )
    expense_total = sum((line.amount for line in expenses), Decimal("0.00"))
    total = labor + expense_total
    return {
        "work_order_id": str(work_order_uuid),
        "labor_cost": labor,
        "expense_total": expense_total,
        "total_cost": total,
    }


def project_cost_summary(db: Session, project_id: str) -> dict:
    project_uuid = coerce_uuid(project_id)
    _ensure_project(db, project_id)
    work_orders = db.query(WorkOrder).filter(WorkOrder.project_id == project_uuid).all()
    labor = Decimal("0.00")
    for order in work_orders:
        logs = db.query(WorkLog).filter(WorkLog.work_order_id == order.id).filter(WorkLog.is_active.is_(True)).all()
        for log in logs:
            rate = log.hourly_rate or _resolve_rate(db, str(log.person_id), log.start_at)
            labor += (Decimal(log.minutes) / Decimal("60.0")) * rate
    expenses = (
        db.query(ExpenseLine).filter(ExpenseLine.project_id == project_id).filter(ExpenseLine.is_active.is_(True)).all()
    )
    expense_total = sum((line.amount for line in expenses), Decimal("0.00"))
    total = labor + expense_total
    return {
        "project_id": project_id,
        "labor_cost": labor,
        "expense_total": expense_total,
        "total_cost": total,
    }


work_logs = WorkLogs()
expense_lines = ExpenseLines()
cost_rates = CostRates()
billing_rates = BillingRates()
