"""Vendor-scoped fiber-plant map data for the field app.

The technician map endpoints are gated by ``require_technician``, so a vendor
crew's Map tab would 403 against them. This router exposes the same
proximity-based fiber-plant read the vendor web portal already grants
(`web_vendor_routes.vendor_fiber_map`), guarded by ``require_vendor_token`` —
so a crew building a route can see the plant around them (OLTs, cabinets,
closures, access/termination points, segments) without the technician-only
mutation endpoints (no location edits, no revert).
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.field import FieldMapAssetNearbyResponse
from app.services.field.map_assets import list_nearby_map_assets
from app.services.vendor_auth_tokens import require_vendor_token

router = APIRouter(prefix="/vendor/map-assets", tags=["field-vendor-map"])


def _parse_types(types: str | None) -> list[str] | None:
    if not types:
        return None
    return [item.strip() for item in types.split(",") if item.strip()]


@router.get("/nearby", response_model=FieldMapAssetNearbyResponse)
def get_vendor_nearby_map_assets(
    lat: float = Query(..., ge=-90, le=90, description="Search-centre latitude (the crew or the project site)."),
    lng: float = Query(..., ge=-180, le=180, description="Search-centre longitude."),
    radius_m: float = Query(default=1000.0, gt=0, le=20000, description="Search radius in metres."),
    types: str | None = Query(default=None, description="Comma-separated asset types to load."),
    limit: int = Query(default=100, ge=1, le=500),
    vendor=Depends(require_vendor_token),
    db: Session = Depends(get_db),
):
    del vendor  # authorization only; the read itself is proximity-scoped
    items = list_nearby_map_assets(
        db,
        latitude=lat,
        longitude=lng,
        radius_m=radius_m,
        asset_types=_parse_types(types),
        limit=limit,
    )
    return {
        "items": items,
        "count": len(items),
        "latitude": lat,
        "longitude": lng,
        "radius_m": radius_m,
        "server_time": datetime.now(UTC),
    }
