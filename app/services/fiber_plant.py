"""Fiber plant service for GeoJSON, statistics, quality checks, and asset merge."""

import json
import logging
import uuid as uuid_mod
from datetime import date, datetime
from enum import Enum as PyEnum

from fastapi import HTTPException
from sqlalchemy import String, func, null, or_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, load_only

from app.models.gis import GeoLocation, ServiceBuilding
from app.models.network import (
    FdhCabinet,
    FiberAccessPoint,
    FiberAssetMergeLog,
    FiberSegment,
    FiberSplice,
    FiberSpliceClosure,
    FiberSpliceTray,
    FiberStrand,
    FiberTerminationPoint,
    OLTDevice,
    OltPowerUnit,
    OltShelf,
    PonPort,
    Splitter,
)
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


def _json_safe(value: object):
    """Convert a value to a JSON-safe representation."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, uuid_mod.UUID):
        return str(value)
    if isinstance(value, PyEnum):
        return _json_safe(value.value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(v) for v in value]
    return str(value)


# ── Mergeable asset type registry ────────────────────────────────────────────
# Each entry: model, FK children [(ChildModel, "fk_column")], mergeable field names
MERGEABLE_ASSET_TYPES: dict[str, dict] = {
    "fdh_cabinet": {
        "model": FdhCabinet,
        "children": [
            (Splitter, "fdh_id"),
        ],
        "fields": ["name", "code", "region_id", "latitude", "longitude", "notes"],
    },
    "olt_device": {
        "model": OLTDevice,
        "children": [
            (OltShelf, "olt_id"),
            (OltPowerUnit, "olt_id"),
            (PonPort, "olt_id"),
        ],
        "fields": ["name", "hostname", "mgmt_ip", "vendor", "model", "serial_number", "latitude", "longitude", "notes"],
    },
    "splice_closure": {
        "model": FiberSpliceClosure,
        "children": [
            (FiberSplice, "closure_id"),
            (FiberSpliceTray, "closure_id"),
        ],
        "fields": ["name", "latitude", "longitude", "notes"],
    },
    "access_point": {
        "model": FiberAccessPoint,
        "children": [],
        "fields": [
            "name",
            "code",
            "access_point_type",
            "placement",
            "latitude",
            "longitude",
            "street",
            "city",
            "county",
            "state",
            "notes",
        ],
    },
    "service_building": {
        "model": ServiceBuilding,
        "children": [],
        "fields": [
            "name",
            "code",
            "clli",
            "latitude",
            "longitude",
            "street",
            "city",
            "state",
            "zip_code",
            "notes",
        ],
    },
}


class FiberPlantManager:
    """Manages fiber plant GeoJSON and statistics."""

    def get_geojson(
        self,
        db: Session,
        include_fdh: bool = True,
        include_closures: bool = True,
        include_pops: bool = True,
        include_segments: bool = True,
        include_access_points: bool = True,
        include_buildings: bool = True,
    ) -> dict:
        """Return all fiber plant assets as a GeoJSON FeatureCollection."""
        features = []

        if include_fdh:
            features.extend(self._get_fdh_features(db))

        if include_closures:
            features.extend(self._get_closure_features(db))

        if include_pops:
            features.extend(self._get_olt_features(db))

        if include_segments:
            features.extend(self._get_segment_features(db))

        if include_access_points:
            features.extend(self._get_access_point_features(db))

        if include_buildings:
            features.extend(self._get_building_features(db))

        return {"type": "FeatureCollection", "features": features}

    def _get_fdh_features(self, db: Session) -> list[dict]:
        """Get FDH cabinet GeoJSON features with batch-loaded splitter counts."""
        fdh_cabinets = (
            db.query(FdhCabinet)
            .options(
                load_only(
                    FdhCabinet.id,
                    FdhCabinet.name,
                    FdhCabinet.code,
                    FdhCabinet.latitude,
                    FdhCabinet.longitude,
                    FdhCabinet.notes,
                )
            )
            .filter(
                FdhCabinet.is_active.is_(True),
                FdhCabinet.latitude.isnot(None),
                FdhCabinet.longitude.isnot(None),
            )
            .all()
        )
        if not fdh_cabinets:
            return []

        # Batch load splitter counts in a single query
        fdh_ids = [fdh.id for fdh in fdh_cabinets]
        splitter_counts = {
            row[0]: row[1]
            for row in db.query(Splitter.fdh_id, func.count(Splitter.id))
            .filter(Splitter.fdh_id.in_(fdh_ids))
            .group_by(Splitter.fdh_id)
            .all()
        }

        return [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [fdh.longitude, fdh.latitude]},
                "properties": {
                    "id": str(fdh.id),
                    "type": "fdh_cabinet",
                    "name": fdh.name,
                    "code": fdh.code,
                    "splitter_count": splitter_counts.get(fdh.id, 0),
                    "notes": fdh.notes,
                },
            }
            for fdh in fdh_cabinets
        ]

    def _get_closure_features(self, db: Session) -> list[dict]:
        """Get splice closure GeoJSON features with batch-loaded counts."""
        closures = (
            db.query(FiberSpliceClosure)
            .options(
                load_only(
                    FiberSpliceClosure.id,
                    FiberSpliceClosure.name,
                    FiberSpliceClosure.latitude,
                    FiberSpliceClosure.longitude,
                    FiberSpliceClosure.notes,
                )
            )
            .filter(
                FiberSpliceClosure.is_active.is_(True),
                FiberSpliceClosure.latitude.isnot(None),
                FiberSpliceClosure.longitude.isnot(None),
            )
            .all()
        )
        if not closures:
            return []

        # Batch load splice and tray counts
        closure_ids = [c.id for c in closures]
        splice_counts = {
            row[0]: row[1]
            for row in db.query(FiberSplice.closure_id, func.count(FiberSplice.id))
            .filter(FiberSplice.closure_id.in_(closure_ids))
            .group_by(FiberSplice.closure_id)
            .all()
        }
        tray_counts = {
            row[0]: row[1]
            for row in db.query(FiberSpliceTray.closure_id, func.count(FiberSpliceTray.id))
            .filter(FiberSpliceTray.closure_id.in_(closure_ids))
            .group_by(FiberSpliceTray.closure_id)
            .all()
        }

        return [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [c.longitude, c.latitude]},
                "properties": {
                    "id": str(c.id),
                    "type": "splice_closure",
                    "name": c.name,
                    "splice_count": splice_counts.get(c.id, 0),
                    "tray_count": tray_counts.get(c.id, 0),
                    "notes": c.notes,
                },
            }
            for c in closures
        ]

    def _get_olt_features(self, db: Session) -> list[dict]:
        """Get OLT device GeoJSON features."""
        olts = (
            db.query(OLTDevice)
            .options(
                load_only(
                    OLTDevice.id,
                    OLTDevice.name,
                    OLTDevice.site_role,
                    OLTDevice.latitude,
                    OLTDevice.longitude,
                    OLTDevice.notes,
                )
            )
            .filter(
                OLTDevice.is_active.is_(True),
                OLTDevice.latitude.isnot(None),
                OLTDevice.longitude.isnot(None),
            )
            .all()
        )
        return [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [olt.longitude, olt.latitude]},
                "properties": {
                    "id": str(olt.id),
                    "type": "olt_device",
                    "name": olt.name,
                    "site_role": getattr(olt, "site_role", "olt") or "olt",
                    "notes": olt.notes,
                },
            }
            for olt in olts
        ]

    def _get_segment_features(self, db: Session) -> list[dict]:
        """Get fiber segment GeoJSON features with batch geometry loading."""
        features = []

        # Batch load segments with PostGIS geometry in a single query
        if self._postgis_available(db):
            rows = (
                db.query(FiberSegment, func.ST_AsGeoJSON(FiberSegment.route_geom))
                .filter(
                    FiberSegment.is_active.is_(True),
                    FiberSegment.route_geom.isnot(None),
                )
                .all()
            )
            for segment, geojson_str in rows:
                if not geojson_str:
                    continue
                features.append(
                    {
                        "type": "Feature",
                        "geometry": json.loads(geojson_str),
                        "properties": {
                            "id": str(segment.id),
                            "type": "fiber_segment",
                            "name": segment.name,
                            "segment_type": segment.segment_type.value if segment.segment_type else None,
                            "cable_type": segment.cable_type.value if segment.cable_type else None,
                            "fiber_count": segment.fiber_count,
                            "length_m": segment.length_m,
                            "notes": segment.notes,
                        },
                    }
                )

        # Also include segments without PostGIS geometry but with termination points
        segments_without_geom = (
            db.query(FiberSegment)
            .options(
                joinedload(FiberSegment.from_point),
                joinedload(FiberSegment.to_point),
            )
            .filter(
                FiberSegment.is_active.is_(True),
                FiberSegment.route_geom.is_(None),
                FiberSegment.from_point_id.isnot(None),
                FiberSegment.to_point_id.isnot(None),
            )
            .all()
        )
        for segment in segments_without_geom:
            fp = segment.from_point
            tp = segment.to_point
            if fp and tp and fp.latitude and fp.longitude and tp.latitude and tp.longitude:
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [fp.longitude, fp.latitude],
                                [tp.longitude, tp.latitude],
                            ],
                        },
                        "properties": {
                            "id": str(segment.id),
                            "type": "fiber_segment",
                            "name": segment.name,
                            "segment_type": segment.segment_type.value if segment.segment_type else None,
                            "cable_type": segment.cable_type.value if segment.cable_type else None,
                            "fiber_count": segment.fiber_count,
                            "length_m": segment.length_m,
                            "notes": segment.notes,
                        },
                    }
                )

        return features

    def _get_access_point_features(self, db: Session) -> list[dict]:
        """Get fiber access point GeoJSON features."""
        access_points = (
            db.query(FiberAccessPoint)
            .options(
                load_only(
                    FiberAccessPoint.id,
                    FiberAccessPoint.name,
                    FiberAccessPoint.code,
                    FiberAccessPoint.access_point_type,
                    FiberAccessPoint.placement,
                    FiberAccessPoint.latitude,
                    FiberAccessPoint.longitude,
                )
            )
            .filter(
                FiberAccessPoint.is_active.is_(True),
                FiberAccessPoint.latitude.isnot(None),
                FiberAccessPoint.longitude.isnot(None),
            )
            .all()
        )
        return [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [ap.longitude, ap.latitude]},
                "properties": {
                    "id": str(ap.id),
                    "type": "access_point",
                    "name": ap.name,
                    "code": ap.code,
                    "ap_type": ap.access_point_type,
                    "placement": ap.placement,
                },
            }
            for ap in access_points
        ]

    def _get_building_features(self, db: Session) -> list[dict]:
        """Get service building GeoJSON features."""
        buildings = (
            db.query(ServiceBuilding)
            .options(
                load_only(
                    ServiceBuilding.id,
                    ServiceBuilding.name,
                    ServiceBuilding.code,
                    ServiceBuilding.latitude,
                    ServiceBuilding.longitude,
                    ServiceBuilding.street,
                    ServiceBuilding.city,
                    ServiceBuilding.notes,
                )
            )
            .filter(
                ServiceBuilding.is_active.is_(True),
                ServiceBuilding.latitude.isnot(None),
                ServiceBuilding.longitude.isnot(None),
            )
            .all()
        )
        return [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [b.longitude, b.latitude]},
                "properties": {
                    "id": str(b.id),
                    "type": "service_building",
                    "name": b.name,
                    "code": b.code,
                    "street": b.street,
                    "city": b.city,
                    "notes": b.notes,
                },
            }
            for b in buildings
        ]

    def get_fdh_splitters(self, db: Session, fdh_id: str) -> list[dict]:
        """Get splitters for a specific FDH cabinet."""
        splitters = db.query(Splitter).filter(Splitter.fdh_id == fdh_id).filter(Splitter.is_active.is_(True)).all()
        return [
            {
                "id": str(s.id),
                "name": s.name,
                "ratio": s.splitter_ratio,
                "input_ports": s.input_ports,
                "output_ports": s.output_ports,
            }
            for s in splitters
        ]

    def get_closure_splices(self, db: Session, closure_id: str) -> list[dict]:
        """Get splices for a specific closure."""
        splices = db.query(FiberSplice).filter(FiberSplice.closure_id == closure_id).all()
        return [
            {
                "id": str(s.id),
                "splice_type": s.splice_type,
                "loss_db": s.loss_db,
                "tray_id": str(s.tray_id) if s.tray_id else None,
            }
            for s in splices
        ]

    def get_stats(self, db: Session) -> dict:
        """Get summary statistics for the fiber plant."""
        fiber_segments_count = db.query(func.count(FiberSegment.id)).filter(FiberSegment.is_active.is_(True)).scalar()
        access_points_count = (
            db.query(func.count(FiberAccessPoint.id)).filter(FiberAccessPoint.is_active.is_(True)).scalar()
        )
        access_points_with_loc = (
            db.query(func.count(FiberAccessPoint.id))
            .filter(FiberAccessPoint.is_active.is_(True), FiberAccessPoint.latitude.isnot(None))
            .scalar()
        )
        olt_count = db.query(func.count(OLTDevice.id)).filter(OLTDevice.is_active.is_(True)).scalar()
        olt_with_loc = (
            db.query(func.count(OLTDevice.id))
            .filter(OLTDevice.is_active.is_(True), OLTDevice.latitude.isnot(None))
            .scalar()
        )
        buildings_count = db.query(func.count(ServiceBuilding.id)).filter(ServiceBuilding.is_active.is_(True)).scalar()
        buildings_with_loc = (
            db.query(func.count(ServiceBuilding.id))
            .filter(ServiceBuilding.is_active.is_(True), ServiceBuilding.latitude.isnot(None))
            .scalar()
        )
        return {
            "fdh_cabinets": db.query(func.count(FdhCabinet.id)).filter(FdhCabinet.is_active.is_(True)).scalar(),
            "fdh_with_location": db.query(func.count(FdhCabinet.id))
            .filter(FdhCabinet.is_active.is_(True), FdhCabinet.latitude.isnot(None))
            .scalar(),
            "splice_closures": db.query(func.count(FiberSpliceClosure.id))
            .filter(FiberSpliceClosure.is_active.is_(True))
            .scalar(),
            "closures_with_location": db.query(func.count(FiberSpliceClosure.id))
            .filter(FiberSpliceClosure.is_active.is_(True), FiberSpliceClosure.latitude.isnot(None))
            .scalar(),
            "splitters": db.query(func.count(Splitter.id)).filter(Splitter.is_active.is_(True)).scalar(),
            "fiber_segments": fiber_segments_count,
            "segments": fiber_segments_count,  # template alias
            "total_splices": db.query(func.count(FiberSplice.id)).scalar(),
            "olt_devices": olt_count,
            "olt_with_location": olt_with_loc,
            "access_points": access_points_count,
            "access_points_with_location": access_points_with_loc,
            "buildings": buildings_count,
            "buildings_with_location": buildings_with_loc,
        }

    def _postgis_available(self, db: Session) -> bool:
        """Check if PostGIS is available in the connected database."""
        if db.bind is None or db.bind.dialect.name != "postgresql":
            return False
        try:
            return db.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'postgis'")).scalar() == 1
        except Exception:
            return False

    def _count_duplicate_values(
        self,
        db: Session,
        model,
        value_column,
        *,
        active_column,
    ) -> tuple[int, int]:
        """Return (duplicate_groups, duplicate_rows) for non-empty normalized values."""
        normalized = func.lower(func.trim(value_column))
        rows = (
            db.query(func.count(model.id))
            .filter(
                active_column.is_(True),
                value_column.isnot(None),
                normalized != "",
            )
            .group_by(normalized)
            .having(func.count(model.id) > 1)
            .all()
        )
        duplicate_groups = len(rows)
        duplicate_rows = int(sum(int(row[0] or 0) for row in rows))
        return duplicate_groups, duplicate_rows

    def get_quality_stats(self, db: Session) -> dict:
        """Return data quality checks for fiber map entities."""
        postgis_available = self._postgis_available(db)

        missing = {
            "fdh_missing_code": db.query(func.count(FdhCabinet.id))
            .filter(
                FdhCabinet.is_active.is_(True),
                or_(FdhCabinet.code.is_(None), func.trim(FdhCabinet.code) == ""),
            )
            .scalar()
            or 0,
            "access_points_missing_code": db.query(func.count(FiberAccessPoint.id))
            .filter(
                FiberAccessPoint.is_active.is_(True),
                or_(
                    FiberAccessPoint.code.is_(None),
                    func.trim(FiberAccessPoint.code) == "",
                ),
            )
            .scalar()
            or 0,
            "splice_closures_missing_name": db.query(func.count(FiberSpliceClosure.id))
            .filter(
                FiberSpliceClosure.is_active.is_(True),
                or_(
                    FiberSpliceClosure.name.is_(None),
                    func.trim(FiberSpliceClosure.name) == "",
                ),
            )
            .scalar()
            or 0,
            "segments_missing_name": db.query(func.count(FiberSegment.id))
            .filter(
                FiberSegment.is_active.is_(True),
                or_(FiberSegment.name.is_(None), func.trim(FiberSegment.name) == ""),
            )
            .scalar()
            or 0,
        }
        missing["total"] = int(sum(missing.values()))

        cabinet_dup_groups, cabinet_dup_rows = self._count_duplicate_values(
            db,
            FdhCabinet,
            FdhCabinet.code,
            active_column=FdhCabinet.is_active,
        )
        access_dup_groups, access_dup_rows = self._count_duplicate_values(
            db,
            FiberAccessPoint,
            FiberAccessPoint.code,
            active_column=FiberAccessPoint.is_active,
        )
        closure_dup_groups, closure_dup_rows = self._count_duplicate_values(
            db,
            FiberSpliceClosure,
            FiberSpliceClosure.name,
            active_column=FiberSpliceClosure.is_active,
        )
        duplicate = {
            "cabinet_code_groups": cabinet_dup_groups,
            "cabinet_code_rows": cabinet_dup_rows,
            "access_point_code_groups": access_dup_groups,
            "access_point_code_rows": access_dup_rows,
            "closure_name_groups": closure_dup_groups,
            "closure_name_rows": closure_dup_rows,
        }
        duplicate["total_groups"] = (
            duplicate["cabinet_code_groups"] + duplicate["access_point_code_groups"] + duplicate["closure_name_groups"]
        )
        duplicate["total_rows"] = (
            duplicate["cabinet_code_rows"] + duplicate["access_point_code_rows"] + duplicate["closure_name_rows"]
        )

        geometry = {
            "fdh_missing_location": db.query(func.count(FdhCabinet.id))
            .filter(
                FdhCabinet.is_active.is_(True),
                or_(FdhCabinet.latitude.is_(None), FdhCabinet.longitude.is_(None)),
            )
            .scalar()
            or 0,
            "closures_missing_location": db.query(func.count(FiberSpliceClosure.id))
            .filter(
                FiberSpliceClosure.is_active.is_(True),
                or_(
                    FiberSpliceClosure.latitude.is_(None),
                    FiberSpliceClosure.longitude.is_(None),
                ),
            )
            .scalar()
            or 0,
            "access_points_missing_location": db.query(func.count(FiberAccessPoint.id))
            .filter(
                FiberAccessPoint.is_active.is_(True),
                or_(
                    FiberAccessPoint.latitude.is_(None),
                    FiberAccessPoint.longitude.is_(None),
                ),
            )
            .scalar()
            or 0,
            "segments_missing_geometry": db.query(func.count(FiberSegment.id))
            .filter(
                FiberSegment.is_active.is_(True),
                FiberSegment.route_geom.is_(None),
            )
            .scalar()
            or 0,
            "fdh_out_of_range": db.query(func.count(FdhCabinet.id))
            .filter(
                FdhCabinet.is_active.is_(True),
                FdhCabinet.latitude.isnot(None),
                FdhCabinet.longitude.isnot(None),
                or_(
                    FdhCabinet.latitude < -90,
                    FdhCabinet.latitude > 90,
                    FdhCabinet.longitude < -180,
                    FdhCabinet.longitude > 180,
                ),
            )
            .scalar()
            or 0,
            "closures_out_of_range": db.query(func.count(FiberSpliceClosure.id))
            .filter(
                FiberSpliceClosure.is_active.is_(True),
                FiberSpliceClosure.latitude.isnot(None),
                FiberSpliceClosure.longitude.isnot(None),
                or_(
                    FiberSpliceClosure.latitude < -90,
                    FiberSpliceClosure.latitude > 90,
                    FiberSpliceClosure.longitude < -180,
                    FiberSpliceClosure.longitude > 180,
                ),
            )
            .scalar()
            or 0,
            "access_points_out_of_range": db.query(func.count(FiberAccessPoint.id))
            .filter(
                FiberAccessPoint.is_active.is_(True),
                FiberAccessPoint.latitude.isnot(None),
                FiberAccessPoint.longitude.isnot(None),
                or_(
                    FiberAccessPoint.latitude < -90,
                    FiberAccessPoint.latitude > 90,
                    FiberAccessPoint.longitude < -180,
                    FiberAccessPoint.longitude > 180,
                ),
            )
            .scalar()
            or 0,
            "segments_invalid_geometry": 0,
            "postgis_checks_enabled": postgis_available,
        }
        if postgis_available:
            geometry["segments_invalid_geometry"] = (
                db.query(func.count(FiberSegment.id))
                .filter(
                    FiberSegment.is_active.is_(True),
                    FiberSegment.route_geom.isnot(None),
                    or_(
                        func.ST_IsValid(FiberSegment.route_geom).is_(False),
                        func.ST_IsEmpty(FiberSegment.route_geom).is_(True),
                    ),
                )
                .scalar()
                or 0
            )

        geometry["total_empty"] = (
            geometry["fdh_missing_location"]
            + geometry["closures_missing_location"]
            + geometry["access_points_missing_location"]
            + geometry["segments_missing_geometry"]
        )
        geometry["total_invalid"] = (
            geometry["fdh_out_of_range"]
            + geometry["closures_out_of_range"]
            + geometry["access_points_out_of_range"]
            + geometry["segments_invalid_geometry"]
        )

        return {
            "missing_identifiers": missing,
            "duplicate_identifiers": duplicate,
            "geometry": geometry,
            "total_issues": (
                missing["total"] + duplicate["total_rows"] + geometry["total_empty"] + geometry["total_invalid"]
            ),
        }

    def get_splice_closure_duplicate_rows(self, db: Session) -> dict:
        """Return duplicate splice-closure rows (by normalized name) with locations.

        Mirrors the QA logic: duplicates are computed on `lower(trim(name))`.
        """
        name_expr = func.trim(FiberSpliceClosure.name)
        norm_expr = func.lower(name_expr)

        dup_norms_sq = (
            db.query(norm_expr.label("norm_name"))
            .filter(
                FiberSpliceClosure.is_active.is_(True),
                FiberSpliceClosure.name.isnot(None),
                name_expr != "",
            )
            .group_by(norm_expr)
            .having(func.count(FiberSpliceClosure.id) > 1)
            .subquery()
        )

        postgis_available = self._postgis_available(db)
        geom_wkt_expr = func.ST_AsText(FiberSpliceClosure.geom) if postgis_available else func.cast(null(), String)

        rows = (
            db.query(
                dup_norms_sq.c.norm_name,
                FiberSpliceClosure.id,
                FiberSpliceClosure.name,
                FiberSpliceClosure.latitude,
                FiberSpliceClosure.longitude,
                geom_wkt_expr,
                FiberSpliceClosure.created_at,
                FiberSpliceClosure.updated_at,
            )
            .join(dup_norms_sq, dup_norms_sq.c.norm_name == norm_expr)
            .filter(FiberSpliceClosure.is_active.is_(True))
            .order_by(dup_norms_sq.c.norm_name.asc(), FiberSpliceClosure.name.asc(), FiberSpliceClosure.id.asc())
            .all()
        )

        groups: dict[str, dict] = {}
        for norm_name, cid, name, lat, lng, geom_wkt, created_at, updated_at in rows:
            key = str(norm_name or "")
            g = groups.setdefault(
                key,
                {
                    "norm_name": key,
                    "row_count": 0,
                    "rows": [],
                },
            )
            g["row_count"] += 1
            g["rows"].append(
                {
                    "id": str(cid),
                    "name": name,
                    "latitude": lat,
                    "longitude": lng,
                    "geom_wkt": geom_wkt,
                    "created_at": created_at,
                    "updated_at": updated_at,
                }
            )

        group_list = list(groups.values())
        group_list.sort(key=lambda item: (-int(item["row_count"]), str(item["norm_name"])))
        total_rows = int(sum(int(g["row_count"]) for g in group_list))
        return {
            "total_groups": len(group_list),
            "total_rows": total_rows,
            "groups": group_list,
            "postgis_checks_enabled": postgis_available,
        }

    # ── Asset Merge ────────────────────────────────────────────────────────
    def get_asset_details(self, db: Session, asset_type: str, asset_id: str) -> dict:
        """Return mergeable fields + child counts for an asset."""
        spec = MERGEABLE_ASSET_TYPES.get(asset_type)
        if not spec:
            raise HTTPException(status_code=400, detail=f"Unknown asset type: {asset_type}")

        model = spec["model"]
        asset = db.get(model, coerce_uuid(asset_id))
        if not asset:
            raise HTTPException(status_code=404, detail="Asset not found")

        fields = {}
        for field_name in spec["fields"]:
            fields[field_name] = _json_safe(getattr(asset, field_name, None))

        child_counts = {}
        for child_model, fk_col in spec["children"]:
            count = db.query(func.count(child_model.id)).filter(getattr(child_model, fk_col) == asset.id).scalar() or 0
            child_counts[child_model.__tablename__] = count

        return {
            "id": str(asset.id),
            "asset_type": asset_type,
            "is_active": getattr(asset, "is_active", True),
            "fields": fields,
            "child_counts": child_counts,
        }

    def merge_assets(
        self,
        db: Session,
        asset_type: str,
        source_id: str,
        target_id: str,
        field_choices: dict[str, str],
        merged_by_id: str | None = None,
    ) -> dict:
        """Merge source asset into target. Source is soft-deleted, children migrated."""
        spec = MERGEABLE_ASSET_TYPES.get(asset_type)
        if not spec:
            raise HTTPException(status_code=400, detail=f"Unknown asset type: {asset_type}")

        model = spec["model"]
        source_uuid = coerce_uuid(source_id)
        target_uuid = coerce_uuid(target_id)

        if source_uuid == target_uuid:
            raise HTTPException(status_code=400, detail="Source and target must be different assets")

        source = db.get(model, source_uuid, with_for_update=True)
        target = db.get(model, target_uuid, with_for_update=True)
        if not source or not target:
            raise HTTPException(status_code=404, detail="Source or target asset not found")
        if not getattr(source, "is_active", True):
            raise HTTPException(status_code=400, detail="Source asset is already inactive")
        if not getattr(target, "is_active", True):
            raise HTTPException(status_code=400, detail="Target asset is already inactive")

        # 1. Snapshot source before merge
        source_snapshot = {}
        for col in source.__table__.columns:
            if not col.name.startswith("_"):
                source_snapshot[col.name] = _json_safe(getattr(source, col.name, None))

        # 2. Apply field choices: copy source values to target where choice == "source"
        for field_name, choice in field_choices.items():
            if field_name not in spec["fields"]:
                continue
            if choice == "source":
                setattr(target, field_name, getattr(source, field_name))

        # 3. Migrate FK children
        children_migrated = {}
        for child_model, fk_col in spec["children"]:
            try:
                count = (
                    db.query(child_model)
                    .filter(getattr(child_model, fk_col) == source_uuid)
                    .update({fk_col: target_uuid})
                )
                children_migrated[child_model.__tablename__] = count
            except IntegrityError:
                db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail=f"Cannot migrate {child_model.__tablename__}: unique constraint conflict",
                )

        # 4. Migrate polymorphic references
        poly_migrated = self._migrate_polymorphic_refs(db, asset_type, source_uuid, target_uuid)
        children_migrated.update(poly_migrated)

        # 5. Soft-delete source
        source.is_active = False

        # 6. Create audit log
        merge_log = FiberAssetMergeLog(
            asset_type=asset_type,
            source_asset_id=source_uuid,
            target_asset_id=target_uuid,
            merged_by_id=coerce_uuid(merged_by_id) if merged_by_id else None,
            source_snapshot=source_snapshot,
            field_choices=field_choices,
            children_migrated=children_migrated,
        )
        db.add(merge_log)
        db.commit()

        logger.info(
            "Merged %s %s → %s (log=%s)",
            asset_type,
            source_id,
            target_id,
            merge_log.id,
        )

        return {
            "merge_log_id": str(merge_log.id),
            "target_id": str(target_uuid),
            "children_migrated": children_migrated,
        }

    def _migrate_polymorphic_refs(self, db: Session, asset_type: str, source_id, target_id) -> dict[str, int]:
        """Migrate polymorphic FK references (GeoLocation, FiberStrand, FiberTerminationPoint)."""
        migrated: dict[str, int] = {}

        # GeoLocation has nullable olt_device_id / fdh_cabinet_id columns
        if asset_type == "olt_device":
            count = (
                db.query(GeoLocation)
                .filter(GeoLocation.olt_device_id == source_id)
                .update({"olt_device_id": target_id})
            )
            if count:
                migrated["geo_locations_olt"] = count
        elif asset_type == "fdh_cabinet":
            count = (
                db.query(GeoLocation)
                .filter(GeoLocation.fdh_cabinet_id == source_id)
                .update({"fdh_cabinet_id": target_id})
            )
            if count:
                migrated["geo_locations_fdh"] = count

        # FiberStrand has polymorphic upstream_id / downstream_id (UUID, no FK constraint)
        for col_name in ("upstream_id", "downstream_id"):
            count = (
                db.query(FiberStrand).filter(getattr(FiberStrand, col_name) == source_id).update({col_name: target_id})
            )
            if count:
                migrated[f"fiber_strands_{col_name}"] = count

        # FiberTerminationPoint has polymorphic ref_id (UUID, no FK constraint)
        count = (
            db.query(FiberTerminationPoint)
            .filter(FiberTerminationPoint.ref_id == source_id)
            .update({"ref_id": target_id})
        )
        if count:
            migrated["fiber_termination_points"] = count

        return migrated


fiber_plant = FiberPlantManager()
