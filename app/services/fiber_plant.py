"""Fiber plant service for GeoJSON, statistics, and quality checks."""

import json

from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from app.models.network import (
    FdhCabinet,
    FiberAccessPoint,
    FiberSegment,
    FiberSplice,
    FiberSpliceClosure,
    FiberSpliceTray,
    OLTDevice,
    Splitter,
)


class FiberPlantManager:
    """Manages fiber plant GeoJSON and statistics."""

    def get_geojson(
        self,
        db: Session,
        include_fdh: bool = True,
        include_closures: bool = True,
        include_pops: bool = True,
        include_segments: bool = True,
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

        return {"type": "FeatureCollection", "features": features}

    def _get_fdh_features(self, db: Session) -> list[dict]:
        """Get FDH cabinet GeoJSON features."""
        fdh_cabinets = (
            db.query(FdhCabinet)
            .filter(FdhCabinet.is_active.is_(True))
            .filter(FdhCabinet.latitude.isnot(None))
            .filter(FdhCabinet.longitude.isnot(None))
            .all()
        )
        features = []
        for fdh in fdh_cabinets:
            splitter_count = db.query(func.count(Splitter.id)).filter(Splitter.fdh_id == fdh.id).scalar()
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [fdh.longitude, fdh.latitude],
                    },
                    "properties": {
                        "id": str(fdh.id),
                        "type": "fdh_cabinet",
                        "name": fdh.name,
                        "code": fdh.code,
                        "splitter_count": splitter_count,
                        "notes": fdh.notes,
                    },
                }
            )
        return features

    def _get_closure_features(self, db: Session) -> list[dict]:
        """Get splice closure GeoJSON features."""
        closures = (
            db.query(FiberSpliceClosure)
            .filter(FiberSpliceClosure.is_active.is_(True))
            .filter(FiberSpliceClosure.latitude.isnot(None))
            .filter(FiberSpliceClosure.longitude.isnot(None))
            .all()
        )
        features = []
        for closure in closures:
            splice_count = db.query(func.count(FiberSplice.id)).filter(FiberSplice.closure_id == closure.id).scalar()
            tray_count = (
                db.query(func.count(FiberSpliceTray.id)).filter(FiberSpliceTray.closure_id == closure.id).scalar()
            )
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [closure.longitude, closure.latitude],
                    },
                    "properties": {
                        "id": str(closure.id),
                        "type": "splice_closure",
                        "name": closure.name,
                        "splice_count": splice_count,
                        "tray_count": tray_count,
                        "notes": closure.notes,
                    },
                }
            )
        return features

    def _get_olt_features(self, db: Session) -> list[dict]:
        """Get OLT device GeoJSON features."""
        olts = (
            db.query(OLTDevice)
            .filter(OLTDevice.is_active.is_(True))
            .filter(OLTDevice.latitude.isnot(None))
            .filter(OLTDevice.longitude.isnot(None))
            .all()
        )
        return [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [olt.longitude, olt.latitude],
                },
                "properties": {
                    "id": str(olt.id),
                    "type": "olt_device",
                    "name": olt.name,
                    "notes": olt.notes,
                },
            }
            for olt in olts
        ]

    def _get_segment_features(self, db: Session) -> list[dict]:
        """Get fiber segment GeoJSON features."""
        segments = db.query(FiberSegment).filter(FiberSegment.is_active.is_(True)).all()
        features = []
        for segment in segments:
            coords = self._get_segment_geometry(db, segment)
            if coords:
                features.append(
                    {
                        "type": "Feature",
                        "geometry": coords,
                        "properties": {
                            "id": str(segment.id),
                            "type": "fiber_segment",
                            "name": segment.name,
                            "segment_type": segment.segment_type.value if segment.segment_type else None,
                            "length_m": segment.length_m,
                            "notes": segment.notes,
                        },
                    }
                )
        return features

    def _get_segment_geometry(self, db: Session, segment: FiberSegment) -> dict | None:
        """Extract geometry from a fiber segment."""
        if segment.route_geom is not None:
            geojson = db.query(func.ST_AsGeoJSON(segment.route_geom)).scalar()
            if geojson:
                return json.loads(geojson)
        elif segment.from_point and segment.to_point:
            if (
                segment.from_point.latitude
                and segment.from_point.longitude
                and segment.to_point.latitude
                and segment.to_point.longitude
            ):
                return {
                    "type": "LineString",
                    "coordinates": [
                        [segment.from_point.longitude, segment.from_point.latitude],
                        [segment.to_point.longitude, segment.to_point.latitude],
                    ],
                }
        return None

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
            "fiber_segments": db.query(func.count(FiberSegment.id)).filter(FiberSegment.is_active.is_(True)).scalar(),
            "total_splices": db.query(func.count(FiberSplice.id)).scalar(),
            "olt_devices": db.query(func.count(OLTDevice.id)).filter(OLTDevice.is_active.is_(True)).scalar(),
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


fiber_plant = FiberPlantManager()
