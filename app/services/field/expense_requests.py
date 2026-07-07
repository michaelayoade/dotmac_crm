"""Technician-scoped expense request workflows for the field app."""

from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload

from app.models.expense_request import ExpenseRequest, ExpenseRequestStatus
from app.schemas.expense_request import ExpenseCategoryRead, ExpenseRequestCreate
from app.schemas.field import FieldExpenseRequestCreate
from app.services.common import apply_pagination, coerce_uuid, validate_enum
from app.services.expense_requests import expense_requests
from app.services.field.jobs import get_scoped_work_order
from app.services.response import ListResponseMixin


class FieldExpenseRequests(ListResponseMixin):
    @staticmethod
    def _find_by_client_ref(db: Session, person_id: str, client_ref: str | None) -> ExpenseRequest | None:
        if not client_ref:
            return None
        rows = (
            db.query(ExpenseRequest)
            .options(selectinload(ExpenseRequest.items))
            .filter(ExpenseRequest.is_active.is_(True))
            .filter(ExpenseRequest.requested_by_person_id == coerce_uuid(person_id))
            .order_by(ExpenseRequest.created_at.desc())
            .limit(200)
            .all()
        )
        for row in rows:
            metadata = row.metadata_ if isinstance(row.metadata_, dict) else {}
            if metadata.get("client_ref") == client_ref:
                return row
        return None

    @staticmethod
    def _validate_category_rules(db: Session, payload: FieldExpenseRequestCreate) -> None:
        try:
            categories = {item.category_code: item for item in FieldExpenseRequests.list_categories(db)}
        except HTTPException as exc:
            if exc.status_code != 502:
                raise
            categories = {}
        if not categories:
            return

        for item in payload.items:
            category = categories.get(item.category_code)
            if category is None:
                continue
            if category.requires_receipt and not (item.receipt_url or "").strip():
                raise HTTPException(
                    status_code=422,
                    detail=f"{category.category_name or category.category_code} requires a receipt",
                )
            max_amount = category.max_amount_per_claim
            if isinstance(max_amount, Decimal) and item.amount > max_amount:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"{category.category_name or category.category_code} exceeds the "
                        f"maximum claim amount of {max_amount}"
                    ),
                )

    @staticmethod
    def list_mine(
        db: Session,
        person_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> list[ExpenseRequest]:
        query = (
            db.query(ExpenseRequest)
            .options(selectinload(ExpenseRequest.items))
            .filter(ExpenseRequest.is_active.is_(True))
            .filter(ExpenseRequest.requested_by_person_id == coerce_uuid(person_id))
            .order_by(ExpenseRequest.created_at.desc())
        )
        if status:
            query = query.filter(ExpenseRequest.status == validate_enum(status, ExpenseRequestStatus, "status"))
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def get_mine(db: Session, person_id: str, expense_request_id: str) -> ExpenseRequest:
        er = expense_requests.get(db, expense_request_id)
        if er.requested_by_person_id != coerce_uuid(person_id):
            raise HTTPException(status_code=404, detail="Expense request not found")
        return er

    @staticmethod
    def create(
        db: Session,
        person_id: str,
        payload: FieldExpenseRequestCreate,
    ) -> ExpenseRequest:
        existing = FieldExpenseRequests._find_by_client_ref(db, person_id, payload.client_ref)
        if existing is not None:
            return existing

        FieldExpenseRequests._validate_category_rules(db, payload)

        work_order_id = payload.work_order_id
        ticket_id = payload.ticket_id
        project_id = payload.project_id

        if work_order_id:
            work_order = get_scoped_work_order(db, person_id, str(work_order_id))
            ticket_id = ticket_id or work_order.ticket_id
            project_id = project_id or work_order.project_id

        create_payload = ExpenseRequestCreate(
            ticket_id=ticket_id,
            project_id=project_id,
            work_order_id=work_order_id,
            requested_by_person_id=coerce_uuid(person_id),
            purpose=payload.purpose,
            expense_date=payload.expense_date,
            currency=payload.currency,
            notes=payload.notes,
            metadata_={"client_ref": payload.client_ref} if payload.client_ref else None,
            items=payload.items,
        )
        return expense_requests.create(db, create_payload)

    @staticmethod
    def cancel(db: Session, person_id: str, expense_request_id: str) -> ExpenseRequest:
        er = FieldExpenseRequests.get_mine(db, person_id, expense_request_id)
        return expense_requests.cancel(db, str(er.id))

    @staticmethod
    def list_categories(db: Session) -> list[ExpenseCategoryRead]:
        """Expense categories come from ERP; empty when ERP is not configured."""
        from app.services.dotmac_erp import DotMacERPError
        from app.services.dotmac_erp.expense_request_sync import dotmac_erp_expense_request_sync

        try:
            sync_service = dotmac_erp_expense_request_sync(db)
        except ValueError:
            return []

        try:
            raw = sync_service.client.get_expense_categories()
        except DotMacERPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Cannot load expense categories right now: {exc}",
            ) from exc
        finally:
            sync_service.close()

        categories: list[ExpenseCategoryRead] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            code = str(entry.get("category_code") or "").strip()
            if not code:
                continue
            categories.append(
                ExpenseCategoryRead(
                    category_code=code,
                    category_name=str(entry.get("category_name") or code),
                    requires_receipt=bool(entry.get("requires_receipt")),
                    max_amount_per_claim=entry.get("max_amount_per_claim"),
                )
            )
        return categories


field_expense_requests = FieldExpenseRequests()
