"""Push purchase orders to DotMac ERP when work orders are created from approved vendor quotes."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.vendor import ProjectQuote
from app.models.workforce import WorkOrder
from app.services.dotmac_erp.client import DotMacERPClient, DotMacERPTransientError

logger = logging.getLogger(__name__)

_LEGACY_ZERO_QTY_FORCE_ONE_QUOTE_IDS = {
    "3ce82b90-ff32-4117-9d9e-d08f47ae7899",
    "a06c39d6-07d7-476e-8ec9-0e6dffc3ba8e",
    "deba1f0f-f666-4363-95ef-2e5147aed41c",
    "018b64d3-c879-4988-8764-0db3d8d9e27d",
    "a55c9723-969e-4307-8b93-14112dc5e815",
}


@dataclass
class PurchaseOrderSyncResult:
    success: bool = False
    work_order_id: str | None = None
    erp_po_id: str | None = None
    error: str | None = None
    error_type: str | None = None
    status_code: int | None = None


class DotMacERPPurchaseOrderSync:
    """Pushes purchase orders to DotMac ERP from approved vendor quotes."""

    def __init__(self, client: DotMacERPClient, session: Session):
        self.client = client
        self.session = session

    def close(self):
        self.client.close()

    def sync_purchase_order(self, work_order: WorkOrder, quote: ProjectQuote) -> PurchaseOrderSyncResult:
        """Push a purchase order to ERP for a work order created from an approved vendor quote."""
        vendor = quote.vendor
        if not vendor:
            msg = f"Quote has no vendor relation (vendor_id={quote.vendor_id}); skipping PO sync"
            logger.warning("PO_SYNC_SKIP_NO_VENDOR work_order_id=%s vendor_id=%s", work_order.id, quote.vendor_id)
            return PurchaseOrderSyncResult(
                success=False,
                work_order_id=str(work_order.id),
                error=msg,
                error_type="vendor_missing",
            )

        payload = self._map_purchase_order(work_order, quote)
        idempotency_key = f"po-wo-{work_order.id}"

        try:
            response = self.client.create_purchase_order(payload, idempotency_key=idempotency_key)
            erp_po_id = response.get("purchase_order_id") if response else None

            if erp_po_id:
                metadata = dict(work_order.metadata_ or {})
                metadata["erp_po_id"] = erp_po_id
                work_order.metadata_ = metadata
                if quote.project is not None:
                    quote.project.erp_purchase_order_id = erp_po_id
                self.session.commit()

            return PurchaseOrderSyncResult(
                success=True,
                work_order_id=str(work_order.id),
                erp_po_id=erp_po_id,
            )
        except DotMacERPTransientError:
            raise
        except (ConnectionError, TimeoutError, OSError) as e:
            raise DotMacERPTransientError(f"Transient transport error for PO sync WO {work_order.id}: {e}") from e
        except Exception as e:
            logger.error("Failed to sync PO for WO %s to ERP: %s", work_order.id, e)
            return PurchaseOrderSyncResult(
                success=False,
                work_order_id=str(work_order.id),
                error=str(e),
                error_type=type(e).__name__,
                status_code=getattr(e, "status_code", None),
            )

    def _map_purchase_order(self, work_order: WorkOrder, quote: ProjectQuote) -> dict:
        """Map a WorkOrder + ProjectQuote to the ERP purchase order payload."""
        vendor = quote.vendor
        project = work_order.project
        coerce_zero_quantity = str(quote.id) in _LEGACY_ZERO_QTY_FORCE_ONE_QUOTE_IDS

        items = []
        for item in quote.line_items:
            if not item.is_active:
                continue
            quantity = Decimal(item.quantity or 0)
            unit_price = Decimal(item.unit_price or 0)
            amount = Decimal(item.amount or 0)
            item_type = (item.item_type or "").strip()
            description = (item.description or "").strip()
            if quantity <= 0 and not coerce_zero_quantity:
                continue
            if not description and not item_type and unit_price <= 0 and amount <= 0 and not coerce_zero_quantity:
                continue
            if quantity <= 0 and coerce_zero_quantity:
                quantity = Decimal("1.000")
            if not description:
                description = f"{(item_type or 'item').replace('_', ' ').title()} item"
            entry: dict = {
                "item_type": item_type or "item",
                "description": description,
                "quantity": str(quantity),
                "unit_price": str(item.unit_price),
                "amount": str(item.amount),
            }
            if item.cable_type:
                entry["cable_type"] = item.cable_type
            if item.fiber_count is not None:
                entry["fiber_count"] = item.fiber_count
            if item.splice_count is not None:
                entry["splice_count"] = item.splice_count
            if item.notes:
                entry["notes"] = item.notes
            items.append(entry)

        payload: dict = {
            "omni_work_order_id": str(work_order.id),
            "omni_quote_id": str(quote.id),
            "vendor_name": vendor.name,
            "title": work_order.title,
            "currency": quote.currency,
            "subtotal": str(quote.subtotal),
            "tax_total": str(quote.tax_total),
            "total": str(quote.total),
            "items": items,
        }

        if vendor.erp_id:
            payload["vendor_erp_id"] = vendor.erp_id
        vendor_code = (vendor.code or "").strip() or vendor.name
        if vendor_code:
            payload["vendor_code"] = vendor_code

        if project:
            payload["omni_project_id"] = str(project.id)
            if project.code:
                payload["project_code"] = project.code
            if project.name:
                payload["project_name"] = project.name

        if quote.reviewed_at:
            payload["approved_at"] = quote.reviewed_at.isoformat()
        if quote.reviewed_by and quote.reviewed_by.email:
            payload["approved_by_email"] = quote.reviewed_by.email

        return payload


def dotmac_erp_purchase_order_sync(session: Session) -> DotMacERPPurchaseOrderSync:
    """Factory function to create a PurchaseOrder sync service."""
    from app.models.domain_settings import SettingDomain
    from app.services import settings_spec

    base_url = settings_spec.resolve_value(session, SettingDomain.integration, "dotmac_erp_base_url")
    token = settings_spec.resolve_value(session, SettingDomain.integration, "dotmac_erp_token")

    if not base_url or not token:
        raise ValueError("DotMac ERP is not configured (missing base_url or api_key)")

    client = DotMacERPClient(base_url=str(base_url), token=str(token))
    return DotMacERPPurchaseOrderSync(client, session)
