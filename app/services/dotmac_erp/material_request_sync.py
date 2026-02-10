"""Push approved material requests to DotMac ERP."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session, selectinload

from app.models.material_request import MaterialRequest, MaterialRequestItem
from app.services.dotmac_erp.client import DotMacERPClient

logger = logging.getLogger(__name__)


@dataclass
class MaterialRequestSyncResult:
    success: bool = False
    material_request_id: str | None = None
    erp_material_request_id: str | None = None
    error: str | None = None
    error_type: str | None = None
    status_code: int | None = None


class DotMacERPMaterialRequestSync:
    """Pushes approved MaterialRequests to DotMac ERP."""

    def __init__(self, client: DotMacERPClient, session: Session):
        self.client = client
        self.session = session

    def close(self):
        self.client.close()

    def sync_material_request(self, mr: MaterialRequest) -> MaterialRequestSyncResult:
        """Push a single material request to ERP."""
        payload = self._map_material_request(mr)
        idempotency_key = f"mr-{mr.id}"

        try:
            response = self.client.push_material_request(payload, idempotency_key=idempotency_key)
            erp_id = response.get("material_request_id") if response else None

            if erp_id and not mr.erp_material_request_id:
                mr.erp_material_request_id = erp_id
                self.session.commit()

            return MaterialRequestSyncResult(
                success=True,
                material_request_id=str(mr.id),
                erp_material_request_id=erp_id,
            )
        except Exception as e:
            logger.error("Failed to sync material request %s to ERP: %s", mr.id, e)
            return MaterialRequestSyncResult(
                success=False,
                material_request_id=str(mr.id),
                error=str(e),
                error_type=type(e).__name__,
                status_code=getattr(e, "status_code", None),
            )

    def _map_material_request(self, mr: MaterialRequest) -> dict:
        """Map a MaterialRequest to the ERP API payload."""
        items = []
        for item in mr.items:
            inv_item = item.item
            items.append({
                "item_code": inv_item.sku or str(inv_item.id),
                "item_name": inv_item.name,
                "quantity": item.quantity,
                "notes": item.notes,
            })

        payload: dict = {
            "omni_id": str(mr.id),
            "number": mr.number,
            "status": mr.status.value,
            "priority": mr.priority.value,
            "notes": mr.notes,
            "items": items,
        }

        if mr.requested_by:
            payload["requested_by_email"] = mr.requested_by.email
        if mr.approved_by:
            payload["approved_by_email"] = mr.approved_by.email

        if mr.ticket_id:
            payload["ticket_omni_id"] = str(mr.ticket_id)
            if mr.ticket:
                payload["ticket_number"] = mr.ticket.number
        if mr.project_id:
            payload["project_omni_id"] = str(mr.project_id)
            if mr.project:
                payload["project_code"] = mr.project.code
        if mr.work_order_id:
            payload["work_order_omni_id"] = str(mr.work_order_id)

        if mr.submitted_at:
            payload["submitted_at"] = mr.submitted_at.isoformat()
        if mr.approved_at:
            payload["approved_at"] = mr.approved_at.isoformat()

        return payload


def dotmac_erp_material_request_sync(session: Session) -> DotMacERPMaterialRequestSync:
    """Factory function to create a MaterialRequest sync service."""
    from app.models.domain_settings import SettingDomain
    from app.services import settings_spec

    base_url = settings_spec.resolve_value(session, SettingDomain.integration, "dotmac_erp_base_url")
    token = settings_spec.resolve_value(session, SettingDomain.integration, "dotmac_erp_token")

    if not base_url or not token:
        raise ValueError("DotMac ERP is not configured (missing base_url or api_key)")

    client = DotMacERPClient(base_url=str(base_url), token=str(token))
    return DotMacERPMaterialRequestSync(client, session)
