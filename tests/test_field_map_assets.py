import uuid

import pytest
from fastapi import HTTPException

from app.models.network import FdhCabinet, FiberAccessPoint, FiberSpliceClosure, OLTDevice
from app.services.field.map_assets import list_map_assets, update_map_asset_location


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
