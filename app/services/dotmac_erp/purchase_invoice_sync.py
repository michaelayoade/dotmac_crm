"""Push approved vendor purchase invoices to DotMac ERP."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.vendor import VendorPurchaseInvoice
from app.services.dotmac_erp.client import DotMacERPClient
from app.services.storage import storage

logger = logging.getLogger(__name__)


@dataclass
class PurchaseInvoiceSyncResult:
    success: bool = False
    invoice_id: str | None = None
    erp_purchase_invoice_id: str | None = None
    error: str | None = None
    error_type: str | None = None
    status_code: int | None = None


class DotMacERPPurchaseInvoiceSync:
    """Pushes approved vendor purchase invoices to DotMac ERP."""

    def __init__(self, client: DotMacERPClient, session: Session):
        self.client = client
        self.session = session

    def close(self):
        self.client.close()

    def sync_purchase_invoice(self, invoice: VendorPurchaseInvoice) -> PurchaseInvoiceSyncResult:
        """Push a single approved purchase invoice to ERP."""
        if invoice.erp_purchase_invoice_id:
            return PurchaseInvoiceSyncResult(
                success=True,
                invoice_id=str(invoice.id),
                erp_purchase_invoice_id=invoice.erp_purchase_invoice_id,
            )

        try:
            payload = self._map_purchase_invoice(invoice)
            response = self.client.create_purchase_invoice(
                payload,
                idempotency_key=f"pinv-{invoice.id}",
            )
            erp_id = (
                response.get("purchase_invoice_id")
                or response.get("invoice_id")
                or response.get("name")
                if response
                else None
            )
            if not erp_id:
                raise ValueError("ERP purchase invoice creation returned no purchase_invoice_id")

            invoice.erp_purchase_invoice_id = str(erp_id)
            invoice.erp_sync_error = None
            invoice.erp_synced_at = datetime.now(UTC)
            self.session.commit()

            if invoice.attachment_storage_key:
                self._upload_attachment(invoice, str(erp_id))

            self.session.refresh(invoice)
            return PurchaseInvoiceSyncResult(
                success=True,
                invoice_id=str(invoice.id),
                erp_purchase_invoice_id=str(erp_id),
            )
        except Exception as exc:
            self.session.rollback()
            try:
                invoice.erp_sync_error = str(exc)[:500]
                self.session.commit()
            except Exception:
                self.session.rollback()
            logger.error("Failed to sync purchase invoice %s to ERP: %s", invoice.id, exc)
            return PurchaseInvoiceSyncResult(
                success=False,
                invoice_id=str(invoice.id),
                erp_purchase_invoice_id=invoice.erp_purchase_invoice_id,
                error=str(exc),
                error_type=type(exc).__name__,
                status_code=getattr(exc, "status_code", None),
            )

    def _map_purchase_invoice(self, invoice: VendorPurchaseInvoice) -> dict:
        project = invoice.project
        base_project = project.project if project else None
        vendor = invoice.vendor
        if not project or not base_project:
            raise ValueError("Purchase invoice project context is missing")
        if not vendor:
            raise ValueError("Purchase invoice vendor context is missing")
        erp_po_id = (invoice.erp_purchase_order_id or project.erp_purchase_order_id or "").strip()
        if not erp_po_id:
            raise ValueError("Installation project has no ERP purchase order ID")

        items: list[dict] = []
        for item in invoice.line_items:
            if not item.is_active:
                continue
            description = (item.description or "").strip()
            item_type = (item.item_type or "").strip()
            if not description and not item_type:
                continue
            items.append(
                {
                    "item_type": item_type or "item",
                    "description": description or f"{(item_type or 'item').replace('_', ' ').title()} item",
                    "quantity": str(item.quantity),
                    "unit_price": str(item.unit_price),
                    "amount": str(item.amount),
                    "notes": item.notes,
                }
            )
        if not items:
            raise ValueError("Purchase invoice has no active line items")

        payload: dict = {
            "crm_invoice_id": str(invoice.id),
            "crm_invoice_number": invoice.invoice_number,
            "crm_project_id": str(base_project.id),
            "installation_project_id": str(project.id),
            "crm_quote_id": str(project.approved_quote_id) if project.approved_quote_id else None,
            "erp_purchase_order_id": erp_po_id,
            "vendor_name": vendor.name,
            "currency": invoice.currency,
            "tax_rate_percent": str(invoice.tax_rate_percent or 0),
            "subtotal": str(invoice.subtotal),
            "tax_total": str(invoice.tax_total),
            "total": str(invoice.total),
            "items": items,
        }

        if vendor.erp_id:
            payload["vendor_erp_id"] = vendor.erp_id
        vendor_code = (vendor.code or "").strip() or vendor.name
        if vendor_code:
            payload["vendor_code"] = vendor_code
        if base_project.code:
            payload["project_code"] = base_project.code
        if base_project.name:
            payload["project_name"] = base_project.name
        if invoice.reviewed_at:
            payload["approved_at"] = invoice.reviewed_at.isoformat()
        if invoice.reviewed_by and invoice.reviewed_by.email:
            payload["approved_by_email"] = invoice.reviewed_by.email

        return payload

    def _upload_attachment(self, invoice: VendorPurchaseInvoice, erp_purchase_invoice_id: str) -> None:
        storage_key = (invoice.attachment_storage_key or "").strip()
        if not storage_key:
            return
        data = storage.get(storage_key)
        payload = {
            "file_name": invoice.attachment_file_name or "purchase-invoice-attachment",
            "mime_type": invoice.attachment_mime_type or "application/octet-stream",
            "content_base64": base64.b64encode(data).decode("ascii"),
        }
        self.client.upload_purchase_invoice_attachment(
            erp_purchase_invoice_id,
            payload,
            idempotency_key=f"pinv-attach-{invoice.id}",
        )


def dotmac_erp_purchase_invoice_sync(session: Session) -> DotMacERPPurchaseInvoiceSync:
    """Factory function to create a PurchaseInvoice sync service."""
    from app.models.domain_settings import SettingDomain
    from app.services import settings_spec

    base_url = settings_spec.resolve_value(session, SettingDomain.integration, "dotmac_erp_base_url")
    token = settings_spec.resolve_value(session, SettingDomain.integration, "dotmac_erp_token")

    if not base_url or not token:
        raise ValueError("DotMac ERP is not configured (missing base_url or api_key)")

    client = DotMacERPClient(base_url=str(base_url), token=str(token))
    return DotMacERPPurchaseInvoiceSync(client, session)
