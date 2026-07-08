"""Expense request workflows.

Field technicians raise expense requests in the CRM; approval and payment
happen in DotMac ERP. Submitted requests are pushed to ERP as expense claims
and their claim status is mirrored back onto the CRM row.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload

from app.models.domain_settings import SettingDomain
from app.models.expense_request import (
    ExpenseRequest,
    ExpenseRequestERPSyncStatus,
    ExpenseRequestItem,
    ExpenseRequestStatus,
)
from app.models.person import Person
from app.schemas.expense_request import ExpenseRequestCreate
from app.services.common import (
    apply_is_active_filter,
    apply_ordering,
    apply_pagination,
    coerce_uuid,
    get_or_404,
    validate_enum,
)
from app.services.numbering import generate_number
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = {
    ExpenseRequestStatus.approved,
    ExpenseRequestStatus.rejected,
    ExpenseRequestStatus.paid,
    ExpenseRequestStatus.canceled,
}


def _enqueue_erp_sync(db: Session, er: ExpenseRequest) -> ExpenseRequest:
    er.erp_sync_status = ExpenseRequestERPSyncStatus.pending
    er.erp_sync_error = None
    db.commit()
    db.refresh(er)

    try:
        from app.tasks.integrations import sync_expense_request_to_erp

        sync_expense_request_to_erp.delay(str(er.id))
    except Exception as exc:
        er.erp_sync_status = ExpenseRequestERPSyncStatus.failed
        er.erp_sync_error = f"ERP sync enqueue failed: {exc}"[:500]
        db.commit()
        db.refresh(er)
        logger.debug("ERP sync enqueue failed for expense request.", exc_info=True)

    return er


class ExpenseRequests(ListResponseMixin):
    @staticmethod
    def create(db: Session, payload: ExpenseRequestCreate) -> ExpenseRequest:
        from app.models.workforce import WorkOrder

        requester = get_or_404(db, Person, str(payload.requested_by_person_id), detail="Person not found")
        if not (requester.email or "").strip():
            raise HTTPException(
                status_code=400,
                detail="Your profile has no email address; one is required to route expenses for approval",
            )

        data = payload.model_dump(exclude={"items"})
        if data.get("work_order_id"):
            work_order = get_or_404(db, WorkOrder, str(data["work_order_id"]), detail="Work order not found")
            data["ticket_id"] = data.get("ticket_id") or work_order.ticket_id
            data["project_id"] = data.get("project_id") or work_order.project_id

        number = generate_number(
            db=db,
            domain=SettingDomain.numbering,
            sequence_key="expense_request_number",
            enabled_key="expense_request_number_enabled",
            prefix_key="expense_request_number_prefix",
            padding_key="expense_request_number_padding",
            start_key="expense_request_number_start",
        )
        if number:
            data["number"] = number
        if not data.get("expense_date"):
            data["expense_date"] = datetime.now(UTC).date()

        er = ExpenseRequest(
            **data,
            status=ExpenseRequestStatus.submitted,
            submitted_at=datetime.now(UTC),
        )
        db.add(er)
        db.flush()

        for item_payload in payload.items:
            db.add(
                ExpenseRequestItem(
                    expense_request_id=er.id,
                    category_code=item_payload.category_code.strip(),
                    category_name=(item_payload.category_name or "").strip() or None,
                    description=item_payload.description.strip(),
                    amount=item_payload.amount,
                    expense_date=item_payload.expense_date or er.expense_date,
                    vendor_name=item_payload.vendor_name,
                    receipt_url=item_payload.receipt_url,
                    notes=item_payload.notes,
                )
            )

        db.commit()
        db.refresh(er)
        return _enqueue_erp_sync(db, er)

    @staticmethod
    def get(db: Session, er_id: str) -> ExpenseRequest:
        return get_or_404(
            db,
            ExpenseRequest,
            er_id,
            options=[selectinload(ExpenseRequest.items)],
        )

    @staticmethod
    def list(
        db: Session,
        is_active: bool | None = None,
        status: str | None = None,
        erp_status: str | None = None,
        ticket_id: str | None = None,
        project_id: str | None = None,
        work_order_id: str | None = None,
        requested_by_person_id: str | None = None,
        created_from: date | None = None,
        created_to: date | None = None,
        order_by: str = "created_at",
        order_dir: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> list[ExpenseRequest]:
        query = db.query(ExpenseRequest).options(selectinload(ExpenseRequest.items))
        query = apply_is_active_filter(query, ExpenseRequest, is_active)
        if status:
            validated_status = validate_enum(status, ExpenseRequestStatus, "status")
            query = query.filter(ExpenseRequest.status == validated_status)
        if erp_status:
            normalized_erp_status = erp_status.strip().lower().replace("-", "_").replace(" ", "_")
            if normalized_erp_status in {item.value for item in ExpenseRequestERPSyncStatus}:
                query = query.filter(
                    ExpenseRequest.erp_sync_status == ExpenseRequestERPSyncStatus(normalized_erp_status)
                )
            else:
                query = query.filter(ExpenseRequest.erp_claim_status == normalized_erp_status)
        if ticket_id:
            query = query.filter(ExpenseRequest.ticket_id == coerce_uuid(ticket_id))
        if project_id:
            query = query.filter(ExpenseRequest.project_id == coerce_uuid(project_id))
        if work_order_id:
            query = query.filter(ExpenseRequest.work_order_id == coerce_uuid(work_order_id))
        if requested_by_person_id:
            query = query.filter(ExpenseRequest.requested_by_person_id == coerce_uuid(requested_by_person_id))

        if created_from and created_to:
            if created_from > created_to:
                raise HTTPException(status_code=400, detail="From date must be before or equal to To date")
            range_start = datetime.combine(created_from, time.min, tzinfo=UTC)
            range_end_exclusive = datetime.combine(created_to + timedelta(days=1), time.min, tzinfo=UTC)
            query = query.filter(ExpenseRequest.created_at >= range_start)
            query = query.filter(ExpenseRequest.created_at < range_end_exclusive)
        query = apply_ordering(
            query,
            order_by,
            order_dir,
            {"created_at": ExpenseRequest.created_at, "expense_date": ExpenseRequest.expense_date},
        )
        return apply_pagination(query, limit, offset).all()

    @staticmethod
    def submit(db: Session, er_id: str) -> ExpenseRequest:
        er = get_or_404(db, ExpenseRequest, er_id, options=[selectinload(ExpenseRequest.items)])
        if er.status != ExpenseRequestStatus.draft:
            raise HTTPException(status_code=400, detail="Only draft expense requests can be submitted")
        if not er.items:
            raise HTTPException(status_code=400, detail="Add at least one expense line before submitting")
        er.status = ExpenseRequestStatus.submitted
        er.submitted_at = datetime.now(UTC)
        db.commit()
        db.refresh(er)
        return _enqueue_erp_sync(db, er)

    @staticmethod
    def retry_erp_sync(db: Session, er_id: str) -> ExpenseRequest:
        er = get_or_404(db, ExpenseRequest, er_id, options=[selectinload(ExpenseRequest.items)])
        if er.status != ExpenseRequestStatus.submitted:
            raise HTTPException(status_code=400, detail="Only submitted expense requests can be synced to ERP")
        return _enqueue_erp_sync(db, er)

    @staticmethod
    def cancel(db: Session, er_id: str) -> ExpenseRequest:
        er = get_or_404(db, ExpenseRequest, er_id, options=[selectinload(ExpenseRequest.items)])
        if er.status in _TERMINAL_STATUSES:
            raise HTTPException(status_code=400, detail=f"Cannot cancel expense request in {er.status.value} status")
        if er.erp_expense_claim_id or er.erp_sync_status == ExpenseRequestERPSyncStatus.synced:
            raise HTTPException(
                status_code=400,
                detail="This expense request already reached ERP; cancel or reject the claim in ERP instead",
            )
        er.status = ExpenseRequestStatus.canceled
        er.erp_sync_status = None
        er.erp_sync_error = None
        db.commit()
        db.refresh(er)
        return er

    @staticmethod
    def approve(db: Session, er_id: str) -> ExpenseRequest:
        er = get_or_404(db, ExpenseRequest, er_id, options=[selectinload(ExpenseRequest.items)])
        if er.status != ExpenseRequestStatus.submitted:
            raise HTTPException(status_code=400, detail="Only submitted expense requests can be approved")
        er.status = ExpenseRequestStatus.approved
        er.approved_at = datetime.now(UTC)
        er.rejection_reason = None
        db.commit()
        db.refresh(er)
        return er

    @staticmethod
    def reject(db: Session, er_id: str, reason: str) -> ExpenseRequest:
        er = get_or_404(db, ExpenseRequest, er_id, options=[selectinload(ExpenseRequest.items)])
        if er.status != ExpenseRequestStatus.submitted:
            raise HTTPException(status_code=400, detail="Only submitted expense requests can be rejected")
        reason = reason.strip()
        if not reason:
            raise HTTPException(status_code=400, detail="Rejection reason is required")
        er.status = ExpenseRequestStatus.rejected
        er.rejected_at = datetime.now(UTC)
        er.rejection_reason = reason[:500]
        db.commit()
        db.refresh(er)
        return er


expense_requests = ExpenseRequests()
