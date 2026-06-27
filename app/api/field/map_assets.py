from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.field import FieldMapAsset, FieldMapAssetListResponse, FieldMapAssetLocationUpdate
from app.services.auth_dependencies import require_user_auth
from app.services.field.map_assets import list_deleted_map_assets, list_map_assets, update_map_asset_location

router = APIRouter(prefix="/map-assets", tags=["field-map-assets"])


def _parse_types(types: str | None) -> list[str] | None:
    if not types:
        return None
    return [item.strip() for item in types.split(",") if item.strip()]


@router.get("", response_model=FieldMapAssetListResponse)
def get_field_map_assets(
    types: str | None = Query(default=None, description="Comma-separated asset types to load."),
    updated_since: datetime | None = Query(default=None, description="Return assets changed after this timestamp."),
    limit: int = Query(default=1000, ge=1, le=2000),
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    del auth
    server_time = datetime.now(UTC)
    asset_types = _parse_types(types)
    items = list_map_assets(db, asset_types=asset_types, updated_since=updated_since, limit=limit)
    deleted = list_deleted_map_assets(db, asset_types=asset_types, deleted_since=updated_since, limit=limit)
    return {
        "items": items,
        "deleted": deleted,
        "count": len(items),
        "limit": limit,
        "offset": 0,
        "server_time": server_time,
    }


@router.patch("/{asset_type}/{asset_id}/location", response_model=FieldMapAsset)
def update_field_map_asset_location(
    asset_type: str,
    asset_id: str,
    payload: FieldMapAssetLocationUpdate,
    auth=Depends(require_user_auth),
    db: Session = Depends(get_db),
):
    return update_map_asset_location(
        db,
        asset_type=asset_type,
        asset_id=asset_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
        actor_id=auth.get("person_id"),
        expected_updated_at=payload.expected_updated_at,
        source=payload.source,
        accuracy_m=payload.accuracy_m,
        client_ref=str(payload.client_ref) if payload.client_ref else None,
    )
