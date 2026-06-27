from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.field import FieldMapAssetTombstone
from app.models.gis import ServiceBuilding
from app.models.network import FdhCabinet, FiberAccessPoint, FiberSpliceClosure, OLTDevice
from app.models.wireless_mast import WirelessMast


@dataclass(frozen=True)
class AssetConfig:
    model: type[Any]
    title_attr: str
    subtitle_attrs: tuple[str, ...] = ()
    status_attr: str | None = "is_active"


ASSET_CONFIGS: dict[str, AssetConfig] = {
    "olt": AssetConfig(OLTDevice, "name", ("hostname", "mgmt_ip", "site_role")),
    "fdh": AssetConfig(FdhCabinet, "name", ("code",)),
    "fiber_access_point": AssetConfig(FiberAccessPoint, "name", ("code", "access_point_type", "placement")),
    "splice_closure": AssetConfig(FiberSpliceClosure, "name"),
    "service_building": AssetConfig(ServiceBuilding, "name", ("code", "clli", "city")),
    "wireless_mast": AssetConfig(WirelessMast, "name", ("structure_type", "owner"), "status"),
}

DEFAULT_ASSET_TYPES = ("olt", "fdh", "fiber_access_point", "splice_closure", "wireless_mast")


def _subtitle(row: Any, attrs: tuple[str, ...]) -> str | None:
    parts = [str(value) for attr in attrs if (value := getattr(row, attr, None))]
    return " · ".join(parts) if parts else None


def _status(row: Any, attr: str | None) -> str | None:
    if not attr:
        return None
    value = getattr(row, attr, None)
    if isinstance(value, bool):
        return "active" if value else "inactive"
    return str(value) if value is not None else None


def _asset_payload(asset_type: str, config: AssetConfig, row: Any) -> dict:
    return {
        "id": row.id,
        "type": asset_type,
        "title": str(getattr(row, config.title_attr, None) or asset_type.replace("_", " ").title()),
        "subtitle": _subtitle(row, config.subtitle_attrs),
        "latitude": float(row.latitude),
        "longitude": float(row.longitude),
        "status": _status(row, config.status_attr),
        "updated_at": getattr(row, "updated_at", None),
    }


def list_map_assets(
    db: Session,
    *,
    asset_types: list[str] | None = None,
    updated_since: datetime | None = None,
    limit: int = 1000,
) -> list[dict]:
    selected = asset_types or list(DEFAULT_ASSET_TYPES)
    items: list[dict] = []
    for asset_type in selected:
        config = ASSET_CONFIGS.get(asset_type)
        if config is None:
            raise HTTPException(status_code=400, detail=f"Unsupported map asset type: {asset_type}")
        remaining = limit - len(items)
        if remaining <= 0:
            break
        query = db.query(config.model).filter(
            config.model.latitude.isnot(None),
            config.model.longitude.isnot(None),
        )
        if hasattr(config.model, "is_active"):
            query = query.filter(config.model.is_active.is_(True))
        if updated_since is not None:
            query = query.filter(config.model.updated_at > updated_since)
        rows = query.order_by(config.model.updated_at.desc()).limit(remaining).all()
        items.extend(_asset_payload(asset_type, config, row) for row in rows)
    return items


def list_deleted_map_assets(
    db: Session,
    *,
    asset_types: list[str] | None = None,
    deleted_since: datetime | None = None,
    limit: int = 1000,
) -> list[dict]:
    query = db.query(FieldMapAssetTombstone)
    if asset_types:
        query = query.filter(FieldMapAssetTombstone.asset_type.in_(asset_types))
    if deleted_since is not None:
        query = query.filter(FieldMapAssetTombstone.deleted_at > deleted_since)
    rows = query.order_by(FieldMapAssetTombstone.deleted_at.desc()).limit(limit).all()
    return [{"type": row.asset_type, "id": row.asset_id, "deleted_at": row.deleted_at} for row in rows]


def record_map_asset_tombstone(db: Session, *, asset_type: str, asset_id) -> None:
    deleted_at = datetime.now(UTC)
    row = (
        db.query(FieldMapAssetTombstone)
        .filter(
            FieldMapAssetTombstone.asset_type == asset_type,
            FieldMapAssetTombstone.asset_id == asset_id,
        )
        .one_or_none()
    )
    if row is None:
        db.add(
            FieldMapAssetTombstone(
                asset_type=asset_type,
                asset_id=asset_id,
                deleted_at=deleted_at,
            )
        )
    else:
        row.deleted_at = deleted_at


def update_map_asset_location(
    db: Session,
    *,
    asset_type: str,
    asset_id: str,
    latitude: float,
    longitude: float,
) -> dict:
    config = ASSET_CONFIGS.get(asset_type)
    if config is None:
        raise HTTPException(status_code=400, detail=f"Unsupported map asset type: {asset_type}")
    row = db.get(config.model, asset_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Map asset not found")
    row.latitude = float(latitude)
    row.longitude = float(longitude)
    db.commit()
    db.refresh(row)
    return _asset_payload(asset_type, config, row)
