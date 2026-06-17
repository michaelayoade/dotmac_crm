from unittest.mock import MagicMock

from app.models.inventory import InventoryLocation
from app.services.dotmac_erp.inventory_sync import DotMacERPInventorySync


def test_sync_locations_stores_erp_warehouse_code(db_session):
    client = MagicMock()
    client.get_inventory_warehouses.return_value = [
        {
            "warehouse_id": "b70bc6b9-1fba-41b3-a335-2bd38d41bd80",
            "code": "Dotmac Garki - DT",
            "name": "Dotmac Garki - DT",
            "is_active": True,
        }
    ]
    sync = DotMacERPInventorySync(db_session)
    sync._client = client

    created, updated, errors = sync.sync_locations()

    assert errors == []
    assert created == 1
    assert updated == 0
    location = db_session.query(InventoryLocation).one()
    assert location.code == "Dotmac Garki - DT"
    assert location.name == "Dotmac Garki - DT"


def test_sync_locations_updates_legacy_warehouse_id_code(db_session):
    legacy = InventoryLocation(
        code="b70bc6b9-1fba-41b3-a335-2bd38d41bd80",
        name="Dotmac Garki - DT",
        is_active=True,
    )
    db_session.add(legacy)
    db_session.commit()

    client = MagicMock()
    client.get_inventory_warehouses.return_value = [
        {
            "warehouse_id": "b70bc6b9-1fba-41b3-a335-2bd38d41bd80",
            "code": "Dotmac Garki - DT",
            "name": "Dotmac Garki - DT",
            "is_active": True,
        }
    ]
    sync = DotMacERPInventorySync(db_session)
    sync._client = client

    created, updated, errors = sync.sync_locations()

    assert errors == []
    assert created == 0
    assert updated == 1
    locations = db_session.query(InventoryLocation).all()
    assert len(locations) == 1
    assert locations[0].id == legacy.id
    assert locations[0].code == "Dotmac Garki - DT"
