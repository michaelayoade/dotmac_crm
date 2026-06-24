from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import ListResponse
from app.schemas.field import FieldMapAsset, FieldMapAssetLocationUpdate
from app.services.auth_dependencies import require_user_auth
from app.services.field.map_assets import list_map_assets, update_map_asset_location

router = APIRouter(prefix="/map-assets", tags=["field-map-assets"])


def _parse_types(types: str | None) -> list[str] | None:
    if not types:
        return None
    return [item.strip() for item in types.split(",") if item.strip()]


@router.get("", response_model=ListResponse[FieldMapAsset])
def get_field_map_assets(
    types: str | None = Query(default=None, description="Comma-separated asset types to load."),
    limit: int = Query(default=1000, ge=1, le=2000),
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    del auth
    items = list_map_assets(db, asset_types=_parse_types(types), limit=limit)
    return {"items": items, "count": len(items), "limit": limit, "offset": 0}


@router.patch("/{asset_type}/{asset_id}/location", response_model=FieldMapAsset)
def update_field_map_asset_location(
    asset_type: str,
    asset_id: str,
    payload: FieldMapAssetLocationUpdate,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    del auth
    return update_map_asset_location(
        db,
        asset_type=asset_type,
        asset_id=asset_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
    )
