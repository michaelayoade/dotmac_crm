from unittest.mock import patch

from app.schemas.inventory import InventoryItemCreate
from app.services.inventory import inventory_items


def test_create_inventory_item_assigns_generated_sku_when_missing(db_session):
    payload = InventoryItemCreate(name="ONT Router", sku=None)
    with patch("app.services.inventory.generate_number", return_value="ITEM-0001"):
        item = inventory_items.create(db_session, payload)
    assert item.sku == "ITEM-0001"


def test_create_inventory_item_keeps_manual_sku(db_session):
    payload = InventoryItemCreate(name="ONT Router", sku="MANUAL-123")
    with patch("app.services.inventory.generate_number", return_value="ITEM-0001") as mocked:
        item = inventory_items.create(db_session, payload)
    assert item.sku == "MANUAL-123"
    mocked.assert_not_called()


def test_create_inventory_item_without_generated_sku_when_disabled(db_session):
    payload = InventoryItemCreate(name="ONT Router", sku=None)
    with patch("app.services.inventory.generate_number", return_value=None):
        item = inventory_items.create(db_session, payload)
    assert item.sku is None
