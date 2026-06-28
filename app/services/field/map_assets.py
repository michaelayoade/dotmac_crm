from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from geoalchemy2.functions import ST_MakePoint, ST_SetSRID
from sqlalchemy.orm import Session

from app.models.audit import AuditActorType, AuditEvent
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


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def update_map_asset_location(
    db: Session,
    *,
    asset_type: str,
    asset_id: str,
    latitude: float,
    longitude: float,
    actor_id: str | None = None,
    expected_updated_at: datetime | None = None,
    source: str | None = None,
    accuracy_m: float | None = None,
    client_ref: str | None = None,
) -> dict:
    config = ASSET_CONFIGS.get(asset_type)
    if config is None:
        raise HTTPException(status_code=400, detail=f"Unsupported map asset type: {asset_type}")
    row = db.get(config.model, asset_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Map asset not found")
    # A tombstoned asset may still be sitting in a device's offline cache; editing
    # it would resurrect a record dispatch has retired. Treat as gone.
    if getattr(row, "is_active", True) is False:
        raise HTTPException(status_code=404, detail="Map asset not found")
    # Optimistic concurrency: when the caller tells us which version it edited,
    # refuse if the asset has moved on since — last-write-wins silently loses a
    # newer correction when two techs (or an offline replay) race.
    if expected_updated_at is not None:
        current_updated_at = _as_utc(getattr(row, "updated_at", None))
        if current_updated_at is not None and current_updated_at != _as_utc(expected_updated_at):
            raise HTTPException(status_code=409, detail="Map asset was modified since it was loaded")

    previous = {
        "latitude": float(row.latitude) if row.latitude is not None else None,
        "longitude": float(row.longitude) if row.longitude is not None else None,
    }
    row.latitude = float(latitude)
    row.longitude = float(longitude)
    # Keep the PostGIS geometry in lockstep with the float columns. Map rendering
    # and ST_DWithin proximity queries read ``geom``; writing only lat/lng leaves
    # the asset showing in two places depending on which column you read.
    if hasattr(config.model, "geom"):
        row.geom = ST_SetSRID(ST_MakePoint(float(longitude), float(latitude)), 4326)

    # Canonical network assets are shared records of truth — every field edit is
    # attributable, with before/after coordinates and pin provenance.
    db.add(
        AuditEvent(
            actor_type=AuditActorType.user if actor_id else AuditActorType.system,
            actor_id=actor_id,
            action="field:map_asset:update_location",
            entity_type=config.model.__name__,
            entity_id=str(asset_id),
            status_code=200,
            is_success=True,
            metadata_={
                "asset_type": asset_type,
                "from": previous,
                "to": {"latitude": float(latitude), "longitude": float(longitude)},
                "source": source,
                "accuracy_m": accuracy_m,
                "client_ref": client_ref,
            },
        )
    )
    db.commit()
    db.refresh(row)
    return _asset_payload(asset_type, config, row)
