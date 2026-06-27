import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from app.models.field import FieldMapAssetTombstone
from app.models.network import FdhCabinet, FiberAccessPoint, FiberSpliceClosure, OLTDevice
from app.services.field.map_assets import (
    list_deleted_map_assets,
    list_map_assets,
    record_map_asset_tombstone,
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
