import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.models.audit import AuditEvent
from app.models.field import FieldMapAssetLocationProvenance, FieldMapAssetTombstone
from app.models.field_location import FieldTechPresence
from app.models.network import (
    FdhCabinet,
    FiberAccessPoint,
    FiberSegment,
    FiberSpliceClosure,
    FiberTerminationPoint,
    OLTDevice,
)
from app.services.field.map_assets import (
    list_deleted_map_assets,
    list_map_assets,
    list_nearby_map_assets,
    record_map_asset_tombstone,
    revert_map_asset_location,
    update_map_asset_location,
)
from app.services.network_impl import fdh_cabinets


def test_list_map_assets_returns_compact_coordinate_payloads(db_session):
    db_session.add_all(
        [
            OLTDevice(name="OLT Alpha", hostname="olt-alpha", latitude=9.1, longitude=7.4),
            FdhCabinet(name="FDH 01", code="FDH-01", latitude=9.2, longitude=7.5),
            FiberAccessPoint(name="FAP 01", code="FAP-01", latitude=None, longitude=None),
            FiberSpliceClosure(name="Closure 01", latitude=9.3, longitude=7.6),
        ]
    )
    db_session.commit()

    items = list_map_assets(db_session, asset_types=["olt", "fdh", "fiber_access_point", "splice_closure"])

    assert [(item["type"], item["title"]) for item in items] == [
        ("olt", "OLT Alpha"),
        ("fdh", "FDH 01"),
        ("splice_closure", "Closure 01"),
    ]
    assert items[0]["subtitle"] == "olt-alpha · olt"
    assert items[0]["latitude"] == 9.1
    assert items[0]["updated_at"] is not None


def test_list_map_assets_filters_by_updated_since(db_session):
    old = OLTDevice(name="OLT Old", latitude=9.1, longitude=7.4)
    new = OLTDevice(name="OLT New", latitude=9.2, longitude=7.5)
    db_session.add_all([old, new])
    db_session.commit()

    old.updated_at = datetime.now(UTC) - timedelta(days=2)
    new.updated_at = datetime.now(UTC)
    db_session.commit()

    items = list_map_assets(
        db_session,
        asset_types=["olt"],
        updated_since=datetime.now(UTC) - timedelta(days=1),
    )

    assert [item["title"] for item in items] == ["OLT New"]


def test_list_map_assets_excludes_inactive_assets(db_session):
    db_session.add_all(
        [
            OLTDevice(name="OLT Active", latitude=9.1, longitude=7.4, is_active=True),
            OLTDevice(name="OLT Inactive", latitude=9.2, longitude=7.5, is_active=False),
        ]
    )
    db_session.commit()

    items = list_map_assets(db_session, asset_types=["olt"])

    assert [item["title"] for item in items] == ["OLT Active"]


def test_list_deleted_map_assets_filters_by_deleted_since(db_session):
    old_id = uuid.uuid4()
    new_id = uuid.uuid4()
    record_map_asset_tombstone(db_session, asset_type="fdh", asset_id=old_id)
    record_map_asset_tombstone(db_session, asset_type="fdh", asset_id=new_id)
    db_session.commit()

    old_row = db_session.query(FieldMapAssetTombstone).filter(FieldMapAssetTombstone.asset_id == old_id).one()
    old_row.deleted_at = datetime.now(UTC) - timedelta(days=2)
    db_session.commit()

    deleted = list_deleted_map_assets(
        db_session,
        asset_types=["fdh"],
        deleted_since=datetime.now(UTC) - timedelta(days=1),
    )

    assert [item["id"] for item in deleted] == [new_id]


def test_list_map_assets_honors_overall_limit(db_session):
    db_session.add_all(
        [
            OLTDevice(name="OLT Alpha", latitude=9.1, longitude=7.4),
            FdhCabinet(name="FDH 01", latitude=9.2, longitude=7.5),
        ]
    )
    db_session.commit()

    items = list_map_assets(db_session, asset_types=["olt", "fdh"], limit=1)

    assert len(items) == 1
    assert items[0]["type"] == "olt"


def test_update_map_asset_location_persists_coordinates(db_session):
    olt = OLTDevice(name="OLT Alpha", latitude=9.1, longitude=7.4)
    db_session.add(olt)
    db_session.commit()

    result = update_map_asset_location(
        db_session,
        asset_type="olt",
        asset_id=str(olt.id),
        latitude=9.501,
        longitude=7.801,
    )

    assert result["latitude"] == 9.501
    assert result["longitude"] == 7.801
    db_session.refresh(olt)
    assert olt.latitude == 9.501
    assert olt.longitude == 7.801


def test_fdh_delete_records_map_asset_tombstone(db_session):
    cabinet = FdhCabinet(name="FDH Delete", latitude=9.2, longitude=7.5)
    db_session.add(cabinet)
    db_session.commit()

    fdh_cabinets.delete(db_session, str(cabinet.id))

    deleted = list_deleted_map_assets(db_session, asset_types=["fdh"])
    assert deleted[0]["id"] == cabinet.id


def test_update_map_asset_location_syncs_geom(db_session):
    cabinet = FdhCabinet(name="FDH Geom", code="FDH-GEOM", latitude=9.2, longitude=7.5)
    db_session.add(cabinet)
    db_session.commit()

    update_map_asset_location(
        db_session,
        asset_type="fdh",
        asset_id=str(cabinet.id),
        latitude=9.6,
        longitude=7.9,
    )

    # geom must track the float columns so ST_DWithin/map queries stay correct.
    # Read via raw SQL so geoalchemy2's geometry result processor doesn't wrap it.
    row = db_session.execute(
        text("SELECT ST_X(geom) AS x, ST_Y(geom) AS y FROM fdh_cabinets WHERE name = :name"),
        {"name": "FDH Geom"},
    ).one()
    assert row.x is not None
    assert round(row.x, 6) == 7.9
    assert round(row.y, 6) == 9.6


def test_update_map_asset_location_writes_audit_event(db_session):
    olt = OLTDevice(name="OLT Audit", latitude=9.1, longitude=7.4)
    db_session.add(olt)
    db_session.commit()

    update_map_asset_location(
        db_session,
        asset_type="olt",
        asset_id=str(olt.id),
        latitude=9.5,
        longitude=7.8,
        actor_id="tech-123",
        source="gps",
        accuracy_m=12.5,
    )

    event = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.action == "field:map_asset:update_location")
        .filter(AuditEvent.entity_id == str(olt.id))
        .one()
    )
    assert event.actor_id == "tech-123"
    assert event.entity_type == "OLTDevice"
    assert event.metadata_["from"] == {"latitude": 9.1, "longitude": 7.4}
    assert event.metadata_["to"] == {"latitude": 9.5, "longitude": 7.8}
    assert event.metadata_["source"] == "gps"
    assert event.metadata_["accuracy_m"] == 12.5


def test_update_map_asset_location_rejects_stale_expected_updated_at(db_session):
    olt = OLTDevice(name="OLT Stale", latitude=9.1, longitude=7.4)
    db_session.add(olt)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        update_map_asset_location(
            db_session,
            asset_type="olt",
            asset_id=str(olt.id),
            latitude=9.5,
            longitude=7.8,
            expected_updated_at=datetime.now(UTC) - timedelta(hours=1),
        )

    assert exc.value.status_code == 409
    db_session.refresh(olt)
    assert olt.latitude == 9.1  # unchanged


def test_update_map_asset_location_accepts_matching_expected_updated_at(db_session):
    olt = OLTDevice(name="OLT Fresh", latitude=9.1, longitude=7.4)
    db_session.add(olt)
    db_session.commit()

    result = update_map_asset_location(
        db_session,
        asset_type="olt",
        asset_id=str(olt.id),
        latitude=9.5,
        longitude=7.8,
        expected_updated_at=olt.updated_at,
    )

    assert result["latitude"] == 9.5


def _provenance(db_session, olt):
    return (
        db_session.query(FieldMapAssetLocationProvenance)
        .filter(
            FieldMapAssetLocationProvenance.asset_type == "olt",
            FieldMapAssetLocationProvenance.asset_id == olt.id,
        )
        .one()
    )


def test_update_map_asset_location_records_provenance(db_session):
    olt = OLTDevice(name="OLT Prov", latitude=9.1, longitude=7.4)
    db_session.add(olt)
    db_session.commit()

    update_map_asset_location(
        db_session,
        asset_type="olt",
        asset_id=str(olt.id),
        latitude=9.5,
        longitude=7.8,
        source="survey",
        accuracy_m=2.0,
    )

    prov = _provenance(db_session, olt)
    assert prov.source == "survey"
    assert prov.accuracy_m == 2.0


def test_update_map_asset_location_blocks_confidence_downgrade(db_session):
    olt = OLTDevice(name="OLT Survey", latitude=9.1, longitude=7.4)
    db_session.add(olt)
    db_session.commit()
    # Surveyed-grade coordinate is placed first.
    update_map_asset_location(
        db_session, asset_type="olt", asset_id=str(olt.id), latitude=9.5, longitude=7.8, source="survey"
    )

    # A phone GPS fix must not silently overwrite it.
    with pytest.raises(HTTPException) as exc:
        update_map_asset_location(
            db_session, asset_type="olt", asset_id=str(olt.id), latitude=9.6, longitude=7.9, source="gps"
        )

    assert exc.value.status_code == 409
    db_session.refresh(olt)
    assert olt.latitude == 9.5  # surveyed coordinate preserved


def test_update_map_asset_location_allows_forced_downgrade(db_session):
    olt = OLTDevice(name="OLT Force", latitude=9.1, longitude=7.4)
    db_session.add(olt)
    db_session.commit()
    update_map_asset_location(
        db_session, asset_type="olt", asset_id=str(olt.id), latitude=9.5, longitude=7.8, source="survey"
    )

    update_map_asset_location(
        db_session, asset_type="olt", asset_id=str(olt.id), latitude=9.6, longitude=7.9, source="gps", force=True
    )

    db_session.refresh(olt)
    assert olt.latitude == 9.6
    assert _provenance(db_session, olt).source == "gps"


def test_update_map_asset_location_allows_confidence_upgrade(db_session):
    olt = OLTDevice(name="OLT Upgrade", latitude=9.1, longitude=7.4)
    db_session.add(olt)
    db_session.commit()
    update_map_asset_location(
        db_session, asset_type="olt", asset_id=str(olt.id), latitude=9.5, longitude=7.8, source="gps"
    )

    # Same or higher trust always applies without a force flag.
    update_map_asset_location(
        db_session, asset_type="olt", asset_id=str(olt.id), latitude=9.6, longitude=7.9, source="manual"
    )

    db_session.refresh(olt)
    assert olt.latitude == 9.6
    assert _provenance(db_session, olt).source == "manual"


def test_update_map_asset_location_rejects_inactive_asset(db_session):
    olt = OLTDevice(name="OLT Gone", latitude=9.1, longitude=7.4, is_active=False)
    db_session.add(olt)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        update_map_asset_location(
            db_session,
            asset_type="olt",
            asset_id=str(olt.id),
            latitude=9.5,
            longitude=7.8,
        )

    assert exc.value.status_code == 404


def test_update_map_asset_location_rejects_unknown_asset(db_session):
    with pytest.raises(HTTPException) as exc:
        update_map_asset_location(
            db_session,
            asset_type="unknown",
            asset_id=str(uuid.uuid4()),
            latitude=9.501,
            longitude=7.801,
        )

    assert exc.value.status_code == 400


def test_revert_restores_previous_coordinate(db_session):
    olt = OLTDevice(name="OLT Revert", latitude=9.1, longitude=7.4)
    db_session.add(olt)
    db_session.commit()
    # A surveyed move, then a revert: revert must bypass the downgrade gate.
    update_map_asset_location(
        db_session, asset_type="olt", asset_id=str(olt.id), latitude=9.5, longitude=7.8, source="survey"
    )

    result = revert_map_asset_location(db_session, asset_type="olt", asset_id=str(olt.id), actor_id="tech-9")

    assert result["latitude"] == 9.1
    assert result["longitude"] == 7.4
    db_session.refresh(olt)
    assert olt.latitude == 9.1
    # The revert is itself an attributable, tagged audit event.
    revert_event = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.action == "field:map_asset:update_location")
        .filter(AuditEvent.entity_id == str(olt.id))
        .order_by(AuditEvent.occurred_at.desc())
        .first()
    )
    assert revert_event.metadata_["source"] == "revert"
    assert revert_event.metadata_.get("revert_of")


def test_revert_with_no_prior_move_is_404(db_session):
    olt = OLTDevice(name="OLT Untouched", latitude=9.1, longitude=7.4)
    db_session.add(olt)
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        revert_map_asset_location(db_session, asset_type="olt", asset_id=str(olt.id))
    assert exc.value.status_code == 404


def test_revert_rejects_when_previous_was_empty(db_session):
    olt = OLTDevice(name="OLT NoPrev", latitude=None, longitude=None)
    db_session.add(olt)
    db_session.commit()
    # First-ever pin: the move's `from` is empty, so there's nothing to revert to.
    update_map_asset_location(db_session, asset_type="olt", asset_id=str(olt.id), latitude=9.5, longitude=7.8)

    with pytest.raises(HTTPException) as exc:
        revert_map_asset_location(db_session, asset_type="olt", asset_id=str(olt.id))
    assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# Move intent: relocation flags connected segments stale
# ---------------------------------------------------------------------------


def _closure_with_segment(db_session):
    closure = FiberSpliceClosure(name="Closure Seg", latitude=9.0, longitude=7.0)
    db_session.add(closure)
    db_session.commit()
    # A termination point that represents the closure, with a segment off it.
    point = FiberTerminationPoint(name="TP", ref_id=closure.id)
    db_session.add(point)
    db_session.commit()
    segment = FiberSegment(name="SEG-1", from_point_id=point.id)
    db_session.add(segment)
    db_session.commit()
    return closure, segment


def test_relocation_flags_connected_segments_stale(db_session):
    closure, segment = _closure_with_segment(db_session)

    update_map_asset_location(
        db_session,
        asset_type="splice_closure",
        asset_id=str(closure.id),
        latitude=9.4,
        longitude=7.4,
        move_type="relocation",
    )

    db_session.refresh(segment)
    assert segment.geometry_stale is True


def test_correction_does_not_flag_segments(db_session):
    closure, segment = _closure_with_segment(db_session)

    update_map_asset_location(
        db_session,
        asset_type="splice_closure",
        asset_id=str(closure.id),
        latitude=9.4,
        longitude=7.4,
        move_type="correction",
    )

    db_session.refresh(segment)
    assert segment.geometry_stale is False
    # Intent is still recorded on the audit trail.
    event = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.entity_id == str(closure.id))
        .order_by(AuditEvent.occurred_at.desc())
        .first()
    )
    assert event.metadata_["move_type"] == "correction"


# ---------------------------------------------------------------------------
# Off-site move review flag
# ---------------------------------------------------------------------------


def _presence(db_session, person, *, lat, lng, at=None):
    db_session.add(
        FieldTechPresence(
            person_id=person.id,
            last_latitude=lat,
            last_longitude=lng,
            last_location_at=at or datetime.now(UTC),
        )
    )
    db_session.commit()


def _last_move_metadata(db_session, olt):
    event = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.action == "field:map_asset:update_location")
        .filter(AuditEvent.entity_id == str(olt.id))
        .order_by(AuditEvent.occurred_at.desc())
        .first()
    )
    return event.metadata_


def test_off_site_low_confidence_move_is_flagged(db_session, person):
    _presence(db_session, person, lat=6.5, lng=3.4)
    olt = OLTDevice(name="OLT Far", latitude=6.5, longitude=3.4)
    db_session.add(olt)
    db_session.commit()

    # Tech is at (6.5, 3.4) but pins the asset ~11 km away with a GPS source.
    update_map_asset_location(
        db_session,
        asset_type="olt",
        asset_id=str(olt.id),
        latitude=6.6,
        longitude=3.4,
        source="gps",
        actor_id=str(person.id),
    )

    meta = _last_move_metadata(db_session, olt)
    assert meta["needs_review"] is True
    assert meta["review_reason"] == "pin_far_from_technician"
    assert meta["tech_distance_m"] > 250


def test_on_site_move_is_not_flagged(db_session, person):
    _presence(db_session, person, lat=6.5, lng=3.4)
    olt = OLTDevice(name="OLT Near", latitude=6.5, longitude=3.4)
    db_session.add(olt)
    db_session.commit()

    # ~200 m from the tech — within the on-site radius.
    update_map_asset_location(
        db_session,
        asset_type="olt",
        asset_id=str(olt.id),
        latitude=6.5018,
        longitude=3.4,
        source="gps",
        actor_id=str(person.id),
    )

    assert "needs_review" not in _last_move_metadata(db_session, olt)


def test_deliberate_manual_move_is_exempt_from_flag(db_session, person):
    _presence(db_session, person, lat=6.5, lng=3.4)
    olt = OLTDevice(name="OLT Manual", latitude=6.5, longitude=3.4)
    db_session.add(olt)
    db_session.commit()

    # Far from the tech, but a deliberate manual placement is not second-guessed.
    update_map_asset_location(
        db_session,
        asset_type="olt",
        asset_id=str(olt.id),
        latitude=6.6,
        longitude=3.4,
        source="manual",
        actor_id=str(person.id),
    )

    assert "needs_review" not in _last_move_metadata(db_session, olt)


def test_stale_presence_does_not_flag(db_session, person):
    _presence(db_session, person, lat=6.5, lng=3.4, at=datetime.now(UTC) - timedelta(hours=2))
    olt = OLTDevice(name="OLT Stale", latitude=6.5, longitude=3.4)
    db_session.add(olt)
    db_session.commit()

    # The tech's last fix is hours old — we can't claim they're off-site.
    update_map_asset_location(
        db_session,
        asset_type="olt",
        asset_id=str(olt.id),
        latitude=6.6,
        longitude=3.4,
        source="gps",
        actor_id=str(person.id),
    )

    assert "needs_review" not in _last_move_metadata(db_session, olt)


# ---------------------------------------------------------------------------
# Nearby / proximity lookup
# ---------------------------------------------------------------------------

# Search centre (Lagos). Offsets below are derived from metres-per-degree so the
# fixtures sit at known distances from this point.
_LAT0, _LNG0 = 6.5, 3.4


def _north(meters: float) -> float:
    return _LAT0 + meters / 111_320.0


def test_list_nearby_returns_assets_within_radius_sorted_by_distance(db_session):
    db_session.add_all(
        [
            OLTDevice(name="At Centre", latitude=_LAT0, longitude=_LNG0),
            OLTDevice(name="300m North", latitude=_north(300), longitude=_LNG0),
            OLTDevice(name="2km North", latitude=_north(2000), longitude=_LNG0),
        ]
    )
    db_session.commit()

    items = list_nearby_map_assets(db_session, latitude=_LAT0, longitude=_LNG0, radius_m=500, asset_types=["olt"])

    # The 2km asset is outside the radius; the rest come back nearest-first.
    assert [item["title"] for item in items] == ["At Centre", "300m North"]
    assert items[0]["distance_m"] == 0.0
    assert 290 <= items[1]["distance_m"] <= 310


def test_list_nearby_excludes_bounding_box_corner_outside_circle(db_session):
    # ~445m north and ~445m east lands inside the square bounding box but ~629m
    # away as the crow flies — haversine must reject it for a 500m radius.
    corner_lat = _north(445)
    corner_lng = _LNG0 + 445 / (111_320.0 * 0.99357)  # cos(6.5°)
    db_session.add_all(
        [
            OLTDevice(name="Centre", latitude=_LAT0, longitude=_LNG0),
            OLTDevice(name="Corner", latitude=corner_lat, longitude=corner_lng),
        ]
    )
    db_session.commit()

    items = list_nearby_map_assets(db_session, latitude=_LAT0, longitude=_LNG0, radius_m=500, asset_types=["olt"])

    assert [item["title"] for item in items] == ["Centre"]


def test_list_nearby_filters_by_type_and_excludes_inactive(db_session):
    db_session.add_all(
        [
            OLTDevice(name="OLT Near", latitude=_LAT0, longitude=_LNG0),
            FdhCabinet(name="FDH Near", latitude=_LAT0, longitude=_LNG0),
            OLTDevice(name="OLT Inactive", latitude=_LAT0, longitude=_LNG0, is_active=False),
        ]
    )
    db_session.commit()

    items = list_nearby_map_assets(db_session, latitude=_LAT0, longitude=_LNG0, radius_m=500, asset_types=["olt"])

    assert [item["title"] for item in items] == ["OLT Near"]


def test_list_nearby_respects_limit(db_session):
    db_session.add_all(
        [
            OLTDevice(name="A", latitude=_north(50), longitude=_LNG0),
            OLTDevice(name="B", latitude=_north(100), longitude=_LNG0),
            OLTDevice(name="C", latitude=_north(150), longitude=_LNG0),
        ]
    )
    db_session.commit()

    items = list_nearby_map_assets(
        db_session, latitude=_LAT0, longitude=_LNG0, radius_m=500, asset_types=["olt"], limit=2
    )

    assert [item["title"] for item in items] == ["A", "B"]


def test_list_nearby_rejects_unknown_asset_type(db_session):
    with pytest.raises(HTTPException) as exc:
        list_nearby_map_assets(db_session, latitude=_LAT0, longitude=_LNG0, radius_m=500, asset_types=["unknown"])

    assert exc.value.status_code == 400
