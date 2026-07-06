"""Push submitted field expense requests to DotMac ERP as expense claims."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.expense_request import ExpenseRequest, ExpenseRequestStatus
from app.services.dotmac_erp.client import (
    DotMacERPClient,
    DotMacERPError,
    DotMacERPTransientError,
)

logger = logging.getLogger(__name__)

# ERP claim statuses that map onto CRM expense request statuses. Anything not
# listed (draft/submitted/pending_approval) keeps the CRM row in `submitted`.
_ERP_TERMINAL_STATUS_MAP = {
    "approved": ExpenseRequestStatus.approved,
    "rejected": ExpenseRequestStatus.rejected,
    "paid": ExpenseRequestStatus.paid,
    "cancelled": ExpenseRequestStatus.canceled,
    "canceled": ExpenseRequestStatus.canceled,
}


@dataclass
class ExpenseRequestSyncResult:
    success: bool = False
    expense_request_id: str | None = None
    erp_expense_claim_id: str | None = None
    erp_claim_status: str | None = None
    error: str | None = None
    error_type: str | None = None
    status_code: int | None = None


class DotMacERPExpenseRequestSync:
    """Pushes submitted ExpenseRequests to DotMac ERP expense claims."""

    def __init__(self, client: DotMacERPClient, session: Session):
        self.client = client
        self.session = session

    def close(self):
        self.client.close()

    def refresh_expense_request_status(self, er: ExpenseRequest) -> ExpenseRequestSyncResult:
        """Refresh the ERP-side approval/payment status for an expense request."""
        response = self.client.get_expense_claim_status(str(er.id))
        if not response:
            return ExpenseRequestSyncResult(
                success=False,
                expense_request_id=str(er.id),
                erp_expense_claim_id=er.erp_expense_claim_id,
                error="Expense claim was not found in ERP",
                error_type="NotFound",
                status_code=404,
            )

        self._apply_erp_response(er, response)
        self.session.commit()

        return ExpenseRequestSyncResult(
            success=True,
            expense_request_id=str(er.id),
            erp_expense_claim_id=er.erp_expense_claim_id,
            erp_claim_status=er.erp_claim_status,
        )

    def sync_expense_request(self, er: ExpenseRequest) -> ExpenseRequestSyncResult:
        """Push a single expense request to ERP."""
        validation_error = self._validate_expense_request_for_sync(er)
        if validation_error:
            return ExpenseRequestSyncResult(
                success=False,
                expense_request_id=str(er.id),
                error=validation_error,
                error_type="ValidationError",
                status_code=422,
            )

        idempotency_key = self._build_idempotency_key(er)
        payload = self._map_expense_request(er)

        try:
            response = self.client.push_expense_claim(payload, idempotency_key=idempotency_key)
            erp_id = self._extract_claim_id(response)

            if not erp_id:
                raise DotMacERPTransientError(f"ERP sync response missing claim_id for expense request {er.id}")

            self._apply_erp_response(er, response)
            self.session.commit()

            return ExpenseRequestSyncResult(
                success=True,
                expense_request_id=str(er.id),
                erp_expense_claim_id=erp_id,
                erp_claim_status=er.erp_claim_status,
            )
        except DotMacERPTransientError:
            raise
        except DotMacERPError as e:
            if self._is_transient_error(e):
                raise DotMacERPTransientError(
                    str(e),
                    status_code=getattr(e, "status_code", None),
                    response=getattr(e, "response", None),
                ) from e

            logger.error("Failed to sync expense request %s to ERP: %s", er.id, e)
            return ExpenseRequestSyncResult(
                success=False,
                expense_request_id=str(er.id),
                error=str(e),
                error_type=type(e).__name__,
                status_code=getattr(e, "status_code", None),
            )
        except (ConnectionError, TimeoutError, OSError) as e:
            raise DotMacERPTransientError(f"Transient transport error for expense request {er.id}: {e}") from e
        except Exception as e:
            logger.error("Failed to sync expense request %s to ERP: %s", er.id, e)
            return ExpenseRequestSyncResult(
                success=False,
                expense_request_id=str(er.id),
                error=str(e),
                error_type=type(e).__name__,
                status_code=getattr(e, "status_code", None),
            )

    def _apply_erp_response(self, er: ExpenseRequest, response: dict) -> None:
        erp_id = self._extract_claim_id(response)
        claim_number = response.get("claim_number")
        claim_status = self._extract_claim_status(response)

        if erp_id and not er.erp_expense_claim_id:
            er.erp_expense_claim_id = erp_id
        if claim_number:
            er.erp_claim_number = str(claim_number)[:60]
        if claim_status:
            er.erp_claim_status = claim_status
            mapped = _ERP_TERMINAL_STATUS_MAP.get(claim_status)
            if mapped and er.status == ExpenseRequestStatus.submitted:
                er.status = mapped
                now = datetime.now(UTC)
                if mapped == ExpenseRequestStatus.approved:
                    er.approved_at = er.approved_at or now
                elif mapped == ExpenseRequestStatus.rejected:
                    er.rejected_at = er.rejected_at or now
                    reason = response.get("rejection_reason")
                    if reason:
                        er.rejection_reason = str(reason)[:500]
                elif mapped == ExpenseRequestStatus.paid:
                    er.approved_at = er.approved_at or now
                    er.paid_at = er.paid_at or now
            elif mapped == ExpenseRequestStatus.paid and er.status == ExpenseRequestStatus.approved:
                er.paid_at = er.paid_at or datetime.now(UTC)
                er.status = mapped

    def _map_expense_request(self, er: ExpenseRequest) -> dict:
        """Map an ExpenseRequest to the ERP expense claim payload."""
        item_rows: list[dict[str, object]] = []
        for item in er.items:
            row: dict[str, object] = {
                "category_code": item.category_code,
                "description": item.description,
                "claimed_amount": str(item.amount),
                "expense_date": (item.expense_date or er.expense_date or er.created_at.date()).isoformat(),
            }
            if item.vendor_name:
                row["vendor_name"] = item.vendor_name
            if item.receipt_url:
                row["receipt_url"] = item.receipt_url
            if item.notes:
                row["notes"] = item.notes
            item_rows.append(row)

        claim_date = (er.expense_date or (er.submitted_at or er.created_at).date()).isoformat()

        return {
            "omni_id": str(er.id),
            "purpose": er.purpose,
            "claim_date": claim_date,
            "requested_by_email": er.requested_by.email if er.requested_by else None,
            "ticket_crm_id": str(er.ticket_id) if er.ticket_id else None,
            "project_crm_id": str(er.project_id) if er.project_id else None,
            "currency_code": er.currency,
            "remarks": er.notes or "",
            "reference_number": er.number,
            "items": item_rows,
        }

    @staticmethod
    def _build_idempotency_key(er: ExpenseRequest) -> str:
        return f"exp-{er.id}-submit-v1"

    @staticmethod
    def _extract_claim_id(response: dict | None) -> str | None:
        if not response or not isinstance(response, dict):
            return None
        erp_id = response.get("claim_id") or response.get("expense_claim_id") or response.get("claim_number")
        return str(erp_id) if erp_id else None

    @staticmethod
    def _extract_claim_status(response: dict | None) -> str | None:
        if not response or not isinstance(response, dict):
            return None
        raw_status = response.get("claim_status") or response.get("status")
        if not raw_status:
            return None
        status = str(raw_status).strip().lower().replace("-", "_").replace(" ", "_")
        return status[:40] if status else None

    @staticmethod
    def _is_transient_error(error: DotMacERPError) -> bool:
        status_code = getattr(error, "status_code", None)
        if status_code in (429, 502, 503, 504):
            return True
        if isinstance(status_code, int) and status_code >= 500:
            return True
        return status_code is None and ("connection" in str(error).lower() or "timeout" in str(error).lower())

    @staticmethod
    def _validate_expense_request_for_sync(er: ExpenseRequest) -> str | None:
        if er.status != ExpenseRequestStatus.submitted:
            return f"Expense request {er.id} is in {er.status.value} status and cannot be synced"
        if not er.items:
            return f"Expense request {er.id} has no expense lines — cannot sync to ERP"
        if not (er.requested_by and (er.requested_by.email or "").strip()):
            return "Requester has no email address; ERP needs it to match the employee"
        return None


def dotmac_erp_expense_request_sync(session: Session) -> DotMacERPExpenseRequestSync:
    """Factory function to create an ExpenseRequest sync service."""
    from app.models.domain_settings import SettingDomain
    from app.services import settings_spec

    base_url = settings_spec.resolve_value(session, SettingDomain.integration, "dotmac_erp_base_url")
    token = settings_spec.resolve_value(session, SettingDomain.integration, "dotmac_erp_token")

    if not base_url or not token:
        raise ValueError("DotMac ERP is not configured (missing base_url or api_key)")

    client = DotMacERPClient(base_url=str(base_url), token=str(token))
    return DotMacERPExpenseRequestSync(client, session)
