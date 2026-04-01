"""Push approved material requests to DotMac ERP."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.material_request import MaterialRequest, MaterialRequestStatus
from app.services.dotmac_erp.client import (
    DotMacERPClient,
    DotMacERPError,
    DotMacERPTransientError,
)

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
        validation_error = self._validate_material_request_for_sync(mr)
        if validation_error:
            return MaterialRequestSyncResult(
                success=False,
                material_request_id=str(mr.id),
                error=validation_error,
                error_type="ValidationError",
                status_code=422,
            )

        idempotency_key = self._build_idempotency_key(mr)
        payload = self._map_material_request(mr, idempotency_key=idempotency_key)

        try:
            response = self.client.push_material_request(payload, idempotency_key=idempotency_key)
            erp_id = self._extract_material_request_id(response)

            if not erp_id:
                raise DotMacERPTransientError(
                    f"ERP sync response missing material_request_id for material request {mr.id}"
                )

            if not mr.erp_material_request_id:
                mr.erp_material_request_id = erp_id
                self.session.commit()

            return MaterialRequestSyncResult(
                success=True,
                material_request_id=str(mr.id),
                erp_material_request_id=erp_id,
            )
        except DotMacERPTransientError:
            raise
        except DotMacERPError as e:
            replay_erp_id = (
                self._extract_material_request_id(e.response) if getattr(e, "status_code", None) == 409 else None
            )
            if replay_erp_id:
                if not mr.erp_material_request_id:
                    mr.erp_material_request_id = replay_erp_id
                    self.session.commit()
                return MaterialRequestSyncResult(
                    success=True,
                    material_request_id=str(mr.id),
                    erp_material_request_id=replay_erp_id,
                )

            if self._is_transient_error(e):
                raise DotMacERPTransientError(
                    str(e),
                    status_code=getattr(e, "status_code", None),
                    response=getattr(e, "response", None),
                ) from e

            logger.error("Failed to sync material request %s to ERP: %s", mr.id, e)
            return MaterialRequestSyncResult(
                success=False,
                material_request_id=str(mr.id),
                error=str(e),
                error_type=type(e).__name__,
                status_code=getattr(e, "status_code", None),
            )
        except (ConnectionError, TimeoutError, OSError) as e:
            raise DotMacERPTransientError(f"Transient transport error for material request {mr.id}: {e}") from e
        except Exception as e:
            logger.error("Failed to sync material request %s to ERP: %s", mr.id, e)
            return MaterialRequestSyncResult(
                success=False,
                material_request_id=str(mr.id),
                error=str(e),
                error_type=type(e).__name__,
                status_code=getattr(e, "status_code", None),
            )

    def _map_material_request(self, mr: MaterialRequest, *, idempotency_key: str) -> dict:
        """Map a MaterialRequest to the ERP API payload."""
        source_warehouse_code = None
        if mr.source_location:
            source_warehouse_code = mr.source_location.code or str(mr.source_location.id)

        destination_warehouse_code = None
        if mr.destination_location:
            destination_warehouse_code = mr.destination_location.code or str(mr.destination_location.id)

        request_type = "TRANSFER" if destination_warehouse_code else "ISSUE"

        item_rows: list[dict[str, object]] = []
        for item in mr.items:
            inv_item = item.item
            item_rows.append(
                {
                    "line_id": str(item.id),
                    "item_code": inv_item.sku or str(inv_item.id),
                    "item_name": inv_item.name,
                    "quantity": item.quantity,
                    "uom": inv_item.unit or "PCS",
                    "from_warehouse_code": source_warehouse_code,
                    "to_warehouse_code": destination_warehouse_code,
                    "notes": item.notes,
                }
            )

        occurred_at = mr.approved_at or mr.submitted_at or mr.created_at or datetime.now(UTC)
        schedule_date = (mr.approved_at or mr.submitted_at or mr.created_at).date().isoformat()

        return {
            "event_type": self._event_type_for_status(mr.status),
            "event_id": str(uuid.uuid4()),
            "idempotency_key": idempotency_key,
            "occurred_at": self._to_iso_z(occurred_at),
            "source_system": "crm",
            "organization_id": self._resolve_organization_id(mr),
            "material_request": {
                "omni_id": str(mr.id),
                "number": mr.number,
                "status": mr.status.value,
                "request_type": request_type,
                "priority": mr.priority.value,
                "schedule_date": schedule_date,
                "remarks": mr.notes,
                "default_from_warehouse_code": source_warehouse_code,
                "default_to_warehouse_code": destination_warehouse_code,
            },
            "items": item_rows,
            "actors": {
                "requested_by_email": mr.requested_by.email if mr.requested_by else None,
                "approved_by_email": mr.approved_by.email if mr.approved_by else None,
            },
            "links": {
                "ticket_omni_id": str(mr.ticket_id) if mr.ticket_id else None,
                "ticket_number": mr.ticket.number if mr.ticket else None,
                "project_omni_id": str(mr.project_id) if mr.project_id else None,
                "project_code": mr.project.code if mr.project else None,
                "work_order_omni_id": str(mr.work_order_id) if mr.work_order_id else None,
            },
        }

    @staticmethod
    def _event_type_for_status(status: MaterialRequestStatus) -> str:
        if status == MaterialRequestStatus.issued:
            return "material_request.issued"
        if status == MaterialRequestStatus.approved:
            return "material_request.approved"
        return f"material_request.{status.value}"

    @staticmethod
    def _build_idempotency_key(mr: MaterialRequest) -> str:
        return f"mr-{mr.id}-approve-v1"

    @staticmethod
    def _extract_material_request_id(response: dict | None) -> str | None:
        if not response or not isinstance(response, dict):
            return None
        erp_id = response.get("material_request_id")
        return str(erp_id) if erp_id else None

    @staticmethod
    def _to_iso_z(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _resolve_organization_id(mr: MaterialRequest) -> str:
        if mr.requested_by and mr.requested_by.organization_id:
            return str(mr.requested_by.organization_id)
        return "00000000-0000-0000-0000-000000000001"

    @staticmethod
    def _is_transient_error(error: DotMacERPError) -> bool:
        status_code = getattr(error, "status_code", None)
        if status_code in (429, 502, 503, 504):
            return True
        if isinstance(status_code, int) and status_code >= 500:
            return True
        return status_code is None and ("connection" in str(error).lower() or "timeout" in str(error).lower())

    @staticmethod
    def _validate_material_request_for_sync(mr: MaterialRequest) -> str | None:
        if mr.status not in (MaterialRequestStatus.approved, MaterialRequestStatus.issued):
            return f"Material request {mr.id} is in {mr.status.value} status and cannot be synced yet"
        if not mr.source_location:
            return "Source warehouse is required before syncing to ERP"
        if mr.destination_location and mr.destination_location_id == mr.source_location_id:
            return "Source and destination warehouse cannot be the same for transfer"
        return None


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
