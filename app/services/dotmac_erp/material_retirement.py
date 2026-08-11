"""Permanent retirement policy for CRM-owned ERP material flows."""

CRM_MATERIAL_ERP_INTEGRATION_RETIRED = True
CRM_MATERIAL_ERP_RETIREMENT_REASON = (
    "CRM material integration is retired; create and reconcile material requests in Selfcare"
)


def retired_material_result(*, material_request_id: str | None = None) -> dict[str, object]:
    """Return the stable no-op result used by legacy Celery entry points."""
    result: dict[str, object] = {
        "success": False,
        "retired": True,
        "error": CRM_MATERIAL_ERP_RETIREMENT_REASON,
    }
    if material_request_id is not None:
        result["material_request_id"] = material_request_id
    return result
