"""Tests for app/services/inventory.py - Coverage 44% -> 90%+"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.inventory import (
    InventoryItem,
    InventoryLocation,
    InventoryStock,
    MaterialStatus,
    Reservation,
    ReservationStatus,
    WorkOrderMaterial,
)
from app.models.workforce import WorkOrder
from app.schemas.inventory import (
    InventoryItemCreate,
    InventoryItemUpdate,
    InventoryLocationCreate,
    InventoryLocationUpdate,
    InventoryStockCreate,
    InventoryStockUpdate,
    ReservationCreate,
    ReservationUpdate,
    WorkOrderMaterialCreate,
    WorkOrderMaterialUpdate,
)
from app.services import inventory as inventory_service
from app.services.common import (
    apply_ordering,
    apply_pagination,
    validate_enum,
)
from app.services.inventory import (
    _ensure_item,
    _ensure_location,
    _ensure_work_order,
    consume_reservation,
    release_reservation,
)


# -----------------------------------------------------------------------------
# Helper function tests
# -----------------------------------------------------------------------------


class TestApplyOrdering:
    """Tests for apply_ordering helper."""

    def testapply_ordering_asc(self, db_session):
        """Test ascending order."""
        query = db_session.query(InventoryItem)
        result = apply_ordering(
            query, "created_at", "asc", {"created_at": InventoryItem.created_at}
        )
        assert result is not None

    def testapply_ordering_desc(self, db_session):
        """Test descending order."""
        query = db_session.query(InventoryItem)
        result = apply_ordering(
            query, "created_at", "desc", {"created_at": InventoryItem.created_at}
        )
        assert result is not None

    def testapply_ordering_invalid_column(self, db_session):
        """Test invalid order_by column."""
        query = db_session.query(InventoryItem)
        with pytest.raises(HTTPException) as exc_info:
            apply_ordering(
                query, "invalid", "asc", {"created_at": InventoryItem.created_at}
            )
        assert exc_info.value.status_code == 400
        assert "Invalid order_by" in exc_info.value.detail


class TestApplyPagination:
    """Tests for apply_pagination helper."""

    def testapply_pagination(self, db_session):
        """Test pagination applies limit and offset."""
        query = db_session.query(InventoryItem)
        result = apply_pagination(query, limit=10, offset=5)
        assert result is not None


class TestValidateEnum:
    """Tests for validate_enum helper."""

    def testvalidate_enum_valid(self):
        """Test valid enum value."""
        result = validate_enum("active", ReservationStatus, "status")
        assert result == ReservationStatus.active

    def testvalidate_enum_none(self):
        """Test None value returns None."""
        result = validate_enum(None, ReservationStatus, "status")
        assert result is None

    def testvalidate_enum_invalid(self):
        """Test invalid enum value raises HTTPException."""
        with pytest.raises(HTTPException) as exc_info:
            validate_enum("invalid_value", ReservationStatus, "status")
        assert exc_info.value.status_code == 400
        assert "Invalid status" in exc_info.value.detail


class TestEnsureHelpers:
    """Tests for _ensure_* helper functions."""

    def test_ensure_item_exists(self, db_session):
        """Test _ensure_item passes for existing item."""
        item = InventoryItem(name="Test Item", sku="TEST-001")
        db_session.add(item)
        db_session.commit()

        # Should not raise
        _ensure_item(db_session, str(item.id))

    def test_ensure_item_not_found(self, db_session):
        """Test _ensure_item raises for non-existent item."""
        with pytest.raises(HTTPException) as exc_info:
            _ensure_item(db_session, str(uuid4()))
        assert exc_info.value.status_code == 404
        assert "Inventory item not found" in exc_info.value.detail

    def test_ensure_location_exists(self, db_session):
        """Test _ensure_location passes for existing location."""
        location = InventoryLocation(name="Warehouse A")
        db_session.add(location)
        db_session.commit()

        # Should not raise
        _ensure_location(db_session, str(location.id))

    def test_ensure_location_not_found(self, db_session):
        """Test _ensure_location raises for non-existent location."""
        with pytest.raises(HTTPException) as exc_info:
            _ensure_location(db_session, str(uuid4()))
        assert exc_info.value.status_code == 404
        assert "Inventory location not found" in exc_info.value.detail

    def test_ensure_work_order_exists(self, db_session, work_order):
        """Test _ensure_work_order passes for existing work order."""
        # Should not raise
        _ensure_work_order(db_session, str(work_order.id))

    def test_ensure_work_order_not_found(self, db_session):
        """Test _ensure_work_order raises for non-existent work order."""
        with pytest.raises(HTTPException) as exc_info:
            _ensure_work_order(db_session, str(uuid4()))
        assert exc_info.value.status_code == 404
        assert "Work order not found" in exc_info.value.detail


# -----------------------------------------------------------------------------
# InventoryItems tests
# -----------------------------------------------------------------------------


class TestInventoryItems:
    """Tests for InventoryItems service class."""

    def test_create_inventory_item(self, db_session):
        """Test creating an inventory item."""
        payload = InventoryItemCreate(
            name="ONT Device",
            sku="ONT-1000",
            description="Optical network terminal",
            unit="each",
        )
        result = inventory_service.inventory_items.create(db_session, payload)
        assert result.id is not None
        assert result.name == "ONT Device"
        assert result.sku == "ONT-1000"

    def test_create_inventory_item_minimal(self, db_session):
        """Test creating item with minimal fields."""
        payload = InventoryItemCreate(name="Basic Item")
        result = inventory_service.inventory_items.create(db_session, payload)
        assert result.name == "Basic Item"
        assert result.is_active is True

    def test_get_inventory_item(self, db_session):
        """Test getting an inventory item by ID."""
        item = InventoryItem(name="Test Item", sku="TST-001")
        db_session.add(item)
        db_session.commit()

        result = inventory_service.inventory_items.get(db_session, str(item.id))
        assert result.id == item.id
        assert result.name == "Test Item"

    def test_get_inventory_item_not_found(self, db_session):
        """Test getting non-existent item."""
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.inventory_items.get(db_session, str(uuid4()))
        assert exc_info.value.status_code == 404

    def test_list_inventory_items_default_active(self, db_session):
        """Test listing defaults to active items."""
        active_item = InventoryItem(name="Active", is_active=True)
        inactive_item = InventoryItem(name="Inactive", is_active=False)
        db_session.add_all([active_item, inactive_item])
        db_session.commit()

        results = inventory_service.inventory_items.list(
            db_session,
            is_active=None,
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        assert all(r.is_active for r in results)

    def test_list_inventory_items_filter_inactive(self, db_session):
        """Test listing inactive items."""
        active_item = InventoryItem(name="Active", is_active=True)
        inactive_item = InventoryItem(name="Inactive", is_active=False)
        db_session.add_all([active_item, inactive_item])
        db_session.commit()

        results = inventory_service.inventory_items.list(
            db_session,
            is_active=False,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(not r.is_active for r in results)

    def test_update_inventory_item(self, db_session):
        """Test updating an inventory item."""
        item = InventoryItem(name="Original", sku="OLD-001")
        db_session.add(item)
        db_session.commit()

        payload = InventoryItemUpdate(name="Updated", sku="NEW-001")
        result = inventory_service.inventory_items.update(db_session, str(item.id), payload)
        assert result.name == "Updated"
        assert result.sku == "NEW-001"

    def test_update_inventory_item_partial(self, db_session):
        """Test partial update."""
        item = InventoryItem(name="Original", sku="SKU-001", description="Desc")
        db_session.add(item)
        db_session.commit()

        payload = InventoryItemUpdate(description="New description")
        result = inventory_service.inventory_items.update(db_session, str(item.id), payload)
        assert result.name == "Original"  # Unchanged
        assert result.description == "New description"

    def test_update_inventory_item_not_found(self, db_session):
        """Test updating non-existent item."""
        payload = InventoryItemUpdate(name="Test")
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.inventory_items.update(db_session, str(uuid4()), payload)
        assert exc_info.value.status_code == 404

    def test_delete_inventory_item(self, db_session):
        """Test soft-deleting an inventory item."""
        item = InventoryItem(name="To Delete", is_active=True)
        db_session.add(item)
        db_session.commit()

        inventory_service.inventory_items.delete(db_session, str(item.id))
        db_session.refresh(item)
        assert item.is_active is False

    def test_delete_inventory_item_not_found(self, db_session):
        """Test deleting non-existent item."""
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.inventory_items.delete(db_session, str(uuid4()))
        assert exc_info.value.status_code == 404


# -----------------------------------------------------------------------------
# InventoryLocations tests
# -----------------------------------------------------------------------------


class TestInventoryLocations:
    """Tests for InventoryLocations service class."""

    def test_create_inventory_location(self, db_session):
        """Test creating a location."""
        payload = InventoryLocationCreate(
            name="Main Warehouse",
            code="WH-001",
        )
        result = inventory_service.inventory_locations.create(db_session, payload)
        assert result.id is not None
        assert result.name == "Main Warehouse"
        assert result.code == "WH-001"

    def test_create_inventory_location_address_not_found(self, db_session):
        """Test creating location with invalid address."""
        payload = InventoryLocationCreate(
            name="Bad Address Location",
            address_id=uuid4(),
        )
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.inventory_locations.create(db_session, payload)
        assert exc_info.value.status_code == 404
        assert "Address not found" in exc_info.value.detail

    def test_get_inventory_location(self, db_session):
        """Test getting a location by ID."""
        location = InventoryLocation(name="Test Location")
        db_session.add(location)
        db_session.commit()

        result = inventory_service.inventory_locations.get(db_session, str(location.id))
        assert result.id == location.id

    def test_get_inventory_location_not_found(self, db_session):
        """Test getting non-existent location."""
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.inventory_locations.get(db_session, str(uuid4()))
        assert exc_info.value.status_code == 404

    def test_list_inventory_locations_default_active(self, db_session):
        """Test listing defaults to active locations."""
        active = InventoryLocation(name="Active", is_active=True)
        inactive = InventoryLocation(name="Inactive", is_active=False)
        db_session.add_all([active, inactive])
        db_session.commit()

        results = inventory_service.inventory_locations.list(
            db_session,
            is_active=None,
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        assert all(r.is_active for r in results)

    def test_list_inventory_locations_filter_inactive(self, db_session):
        """Test listing inactive locations."""
        active = InventoryLocation(name="Active", is_active=True)
        inactive = InventoryLocation(name="Inactive", is_active=False)
        db_session.add_all([active, inactive])
        db_session.commit()

        results = inventory_service.inventory_locations.list(
            db_session,
            is_active=False,
            order_by="created_at",
            order_dir="asc",
            limit=100,
            offset=0,
        )
        assert all(not r.is_active for r in results)

    def test_update_inventory_location(self, db_session):
        """Test updating a location."""
        location = InventoryLocation(name="Original")
        db_session.add(location)
        db_session.commit()

        payload = InventoryLocationUpdate(name="Updated", code="UPD-001")
        result = inventory_service.inventory_locations.update(
            db_session, str(location.id), payload
        )
        assert result.name == "Updated"
        assert result.code == "UPD-001"

    def test_update_inventory_location_address_not_found(self, db_session):
        """Test updating location with invalid address."""
        location = InventoryLocation(name="Location")
        db_session.add(location)
        db_session.commit()

        payload = InventoryLocationUpdate(address_id=uuid4())
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.inventory_locations.update(
                db_session, str(location.id), payload
            )
        assert exc_info.value.status_code == 404
        assert "Address not found" in exc_info.value.detail

    def test_update_inventory_location_not_found(self, db_session):
        """Test updating non-existent location."""
        payload = InventoryLocationUpdate(name="Test")
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.inventory_locations.update(db_session, str(uuid4()), payload)
        assert exc_info.value.status_code == 404

    def test_delete_inventory_location(self, db_session):
        """Test soft-deleting a location."""
        location = InventoryLocation(name="To Delete", is_active=True)
        db_session.add(location)
        db_session.commit()

        inventory_service.inventory_locations.delete(db_session, str(location.id))
        db_session.refresh(location)
        assert location.is_active is False

    def test_delete_inventory_location_not_found(self, db_session):
        """Test deleting non-existent location."""
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.inventory_locations.delete(db_session, str(uuid4()))
        assert exc_info.value.status_code == 404


# -----------------------------------------------------------------------------
# InventoryStocks tests
# -----------------------------------------------------------------------------


class TestInventoryStocks:
    """Tests for InventoryStocks service class."""

    @pytest.fixture()
    def inventory_item(self, db_session):
        """Create inventory item for tests."""
        item = InventoryItem(name="Stock Item", sku="STOCK-001")
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)
        return item

    @pytest.fixture()
    def inventory_location(self, db_session):
        """Create inventory location for tests."""
        location = InventoryLocation(name="Stock Location")
        db_session.add(location)
        db_session.commit()
        db_session.refresh(location)
        return location

    def test_create_inventory_stock(self, db_session, inventory_item, inventory_location):
        """Test creating inventory stock."""
        payload = InventoryStockCreate(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity_on_hand=100,
        )
        result = inventory_service.inventory_stocks.create(db_session, payload)
        assert result.id is not None
        assert result.quantity_on_hand == 100

    def test_create_inventory_stock_item_not_found(self, db_session, inventory_location):
        """Test creating stock with invalid item."""
        payload = InventoryStockCreate(
            item_id=uuid4(),
            location_id=inventory_location.id,
            quantity_on_hand=10,
        )
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.inventory_stocks.create(db_session, payload)
        assert exc_info.value.status_code == 404
        assert "Inventory item not found" in exc_info.value.detail

    def test_create_inventory_stock_location_not_found(self, db_session, inventory_item):
        """Test creating stock with invalid location."""
        payload = InventoryStockCreate(
            item_id=inventory_item.id,
            location_id=uuid4(),
            quantity_on_hand=10,
        )
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.inventory_stocks.create(db_session, payload)
        assert exc_info.value.status_code == 404
        assert "Inventory location not found" in exc_info.value.detail

    def test_get_inventory_stock(self, db_session, inventory_item, inventory_location):
        """Test getting inventory stock by ID."""
        stock = InventoryStock(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity_on_hand=50,
        )
        db_session.add(stock)
        db_session.commit()

        result = inventory_service.inventory_stocks.get(db_session, str(stock.id))
        assert result.id == stock.id
        assert result.quantity_on_hand == 50

    def test_get_inventory_stock_not_found(self, db_session):
        """Test getting non-existent stock."""
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.inventory_stocks.get(db_session, str(uuid4()))
        assert exc_info.value.status_code == 404

    def test_list_inventory_stocks_default_active(
        self, db_session, inventory_item, inventory_location
    ):
        """Test listing defaults to active stocks."""
        active = InventoryStock(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity_on_hand=10,
            is_active=True,
        )
        db_session.add(active)
        db_session.commit()

        results = inventory_service.inventory_stocks.list(
            db_session,
            item_id=None,
            location_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        assert all(r.is_active for r in results)

    def test_list_inventory_stocks_filter_by_item(
        self, db_session, inventory_item, inventory_location
    ):
        """Test filtering stocks by item_id."""
        stock = InventoryStock(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity_on_hand=25,
        )
        db_session.add(stock)
        db_session.commit()

        results = inventory_service.inventory_stocks.list(
            db_session,
            item_id=str(inventory_item.id),
            location_id=None,
            is_active=None,
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        assert all(r.item_id == inventory_item.id for r in results)

    def test_list_inventory_stocks_filter_by_location(
        self, db_session, inventory_item, inventory_location
    ):
        """Test filtering stocks by location_id."""
        stock = InventoryStock(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity_on_hand=25,
        )
        db_session.add(stock)
        db_session.commit()

        results = inventory_service.inventory_stocks.list(
            db_session,
            item_id=None,
            location_id=str(inventory_location.id),
            is_active=None,
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        assert all(r.location_id == inventory_location.id for r in results)

    def test_list_inventory_stocks_filter_inactive(
        self, db_session, inventory_item, inventory_location
    ):
        """Test filtering inactive stocks."""
        inactive = InventoryStock(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity_on_hand=5,
            is_active=False,
        )
        db_session.add(inactive)
        db_session.commit()

        results = inventory_service.inventory_stocks.list(
            db_session,
            item_id=None,
            location_id=None,
            is_active=False,
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        assert all(not r.is_active for r in results)

    def test_update_inventory_stock(self, db_session, inventory_item, inventory_location):
        """Test updating inventory stock."""
        stock = InventoryStock(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity_on_hand=100,
        )
        db_session.add(stock)
        db_session.commit()

        payload = InventoryStockUpdate(quantity_on_hand=150)
        result = inventory_service.inventory_stocks.update(db_session, str(stock.id), payload)
        assert result.quantity_on_hand == 150

    def test_update_inventory_stock_change_item(self, db_session, inventory_location):
        """Test updating stock to different item."""
        item1 = InventoryItem(name="Item 1")
        item2 = InventoryItem(name="Item 2")
        db_session.add_all([item1, item2])
        db_session.commit()

        stock = InventoryStock(
            item_id=item1.id,
            location_id=inventory_location.id,
            quantity_on_hand=10,
        )
        db_session.add(stock)
        db_session.commit()

        payload = InventoryStockUpdate(item_id=item2.id)
        result = inventory_service.inventory_stocks.update(db_session, str(stock.id), payload)
        assert result.item_id == item2.id

    def test_update_inventory_stock_invalid_item(
        self, db_session, inventory_item, inventory_location
    ):
        """Test updating stock with invalid item."""
        stock = InventoryStock(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity_on_hand=10,
        )
        db_session.add(stock)
        db_session.commit()

        payload = InventoryStockUpdate(item_id=uuid4())
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.inventory_stocks.update(db_session, str(stock.id), payload)
        assert exc_info.value.status_code == 404

    def test_update_inventory_stock_change_location(self, db_session, inventory_item):
        """Test updating stock to different location."""
        loc1 = InventoryLocation(name="Location 1")
        loc2 = InventoryLocation(name="Location 2")
        db_session.add_all([loc1, loc2])
        db_session.commit()

        stock = InventoryStock(
            item_id=inventory_item.id,
            location_id=loc1.id,
            quantity_on_hand=10,
        )
        db_session.add(stock)
        db_session.commit()

        payload = InventoryStockUpdate(location_id=loc2.id)
        result = inventory_service.inventory_stocks.update(db_session, str(stock.id), payload)
        assert result.location_id == loc2.id

    def test_update_inventory_stock_invalid_location(
        self, db_session, inventory_item, inventory_location
    ):
        """Test updating stock with invalid location."""
        stock = InventoryStock(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity_on_hand=10,
        )
        db_session.add(stock)
        db_session.commit()

        payload = InventoryStockUpdate(location_id=uuid4())
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.inventory_stocks.update(db_session, str(stock.id), payload)
        assert exc_info.value.status_code == 404

    def test_update_inventory_stock_not_found(self, db_session):
        """Test updating non-existent stock."""
        payload = InventoryStockUpdate(quantity_on_hand=50)
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.inventory_stocks.update(db_session, str(uuid4()), payload)
        assert exc_info.value.status_code == 404

    def test_delete_inventory_stock(self, db_session, inventory_item, inventory_location):
        """Test soft-deleting inventory stock."""
        stock = InventoryStock(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity_on_hand=10,
            is_active=True,
        )
        db_session.add(stock)
        db_session.commit()

        inventory_service.inventory_stocks.delete(db_session, str(stock.id))
        db_session.refresh(stock)
        assert stock.is_active is False

    def test_delete_inventory_stock_not_found(self, db_session):
        """Test deleting non-existent stock."""
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.inventory_stocks.delete(db_session, str(uuid4()))
        assert exc_info.value.status_code == 404


# -----------------------------------------------------------------------------
# Reservations tests
# -----------------------------------------------------------------------------


class TestReservations:
    """Tests for Reservations service class."""

    @pytest.fixture()
    def inventory_item(self, db_session):
        """Create inventory item for tests."""
        item = InventoryItem(name="Reservable Item", sku="RES-001")
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)
        return item

    @pytest.fixture()
    def inventory_location(self, db_session):
        """Create inventory location for tests."""
        location = InventoryLocation(name="Reserve Location")
        db_session.add(location)
        db_session.commit()
        db_session.refresh(location)
        return location

    @pytest.fixture()
    def inventory_stock(self, db_session, inventory_item, inventory_location):
        """Create inventory stock for tests."""
        stock = InventoryStock(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity_on_hand=100,
            reserved_quantity=0,
        )
        db_session.add(stock)
        db_session.commit()
        db_session.refresh(stock)
        return stock

    def test_create_reservation(
        self, db_session, inventory_item, inventory_location, inventory_stock
    ):
        """Test creating a reservation."""
        payload = ReservationCreate(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity=10,
        )
        result = inventory_service.reservations.create(db_session, payload)
        assert result.id is not None
        assert result.quantity == 10
        assert result.status == ReservationStatus.active

        # Check stock was updated
        db_session.refresh(inventory_stock)
        assert inventory_stock.reserved_quantity == 10

    def test_create_reservation_with_work_order(
        self, db_session, inventory_item, inventory_location, inventory_stock, work_order
    ):
        """Test creating reservation with work order."""
        payload = ReservationCreate(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            work_order_id=work_order.id,
            quantity=5,
        )
        result = inventory_service.reservations.create(db_session, payload)
        assert result.work_order_id == work_order.id

    def test_create_reservation_work_order_not_found(
        self, db_session, inventory_item, inventory_location, inventory_stock
    ):
        """Test creating reservation with invalid work order."""
        payload = ReservationCreate(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            work_order_id=uuid4(),
            quantity=5,
        )
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.reservations.create(db_session, payload)
        assert exc_info.value.status_code == 404
        assert "Work order not found" in exc_info.value.detail

    def test_create_reservation_item_not_found(self, db_session, inventory_location):
        """Test creating reservation with invalid item."""
        payload = ReservationCreate(
            item_id=uuid4(),
            location_id=inventory_location.id,
            quantity=5,
        )
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.reservations.create(db_session, payload)
        assert exc_info.value.status_code == 404

    def test_create_reservation_location_not_found(self, db_session, inventory_item):
        """Test creating reservation with invalid location."""
        payload = ReservationCreate(
            item_id=inventory_item.id,
            location_id=uuid4(),
            quantity=5,
        )
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.reservations.create(db_session, payload)
        assert exc_info.value.status_code == 404

    def test_create_reservation_no_stock(self, db_session, inventory_item, inventory_location):
        """Test creating reservation when no stock exists."""
        payload = ReservationCreate(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity=5,
        )
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.reservations.create(db_session, payload)
        assert exc_info.value.status_code == 404
        assert "Inventory stock not found" in exc_info.value.detail

    def test_create_reservation_insufficient_stock(
        self, db_session, inventory_item, inventory_location, inventory_stock
    ):
        """Test creating reservation with insufficient stock."""
        payload = ReservationCreate(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity=200,  # More than available (100)
        )
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.reservations.create(db_session, payload)
        assert exc_info.value.status_code == 400
        assert "Insufficient stock" in exc_info.value.detail

    def test_get_reservation(
        self, db_session, inventory_item, inventory_location, inventory_stock
    ):
        """Test getting a reservation by ID."""
        reservation = Reservation(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity=5,
        )
        db_session.add(reservation)
        db_session.commit()

        result = inventory_service.reservations.get(db_session, str(reservation.id))
        assert result.id == reservation.id

    def test_get_reservation_not_found(self, db_session):
        """Test getting non-existent reservation."""
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.reservations.get(db_session, str(uuid4()))
        assert exc_info.value.status_code == 404

    def test_list_reservations(
        self, db_session, inventory_item, inventory_location, inventory_stock
    ):
        """Test listing reservations."""
        r1 = Reservation(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity=5,
        )
        r2 = Reservation(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity=10,
        )
        db_session.add_all([r1, r2])
        db_session.commit()

        results = inventory_service.reservations.list(
            db_session,
            item_id=None,
            work_order_id=None,
            status=None,
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        assert len(results) >= 2

    def test_list_reservations_filter_by_item(
        self, db_session, inventory_item, inventory_location, inventory_stock
    ):
        """Test filtering reservations by item_id."""
        reservation = Reservation(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity=5,
        )
        db_session.add(reservation)
        db_session.commit()

        results = inventory_service.reservations.list(
            db_session,
            item_id=str(inventory_item.id),
            work_order_id=None,
            status=None,
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        assert all(r.item_id == inventory_item.id for r in results)

    def test_list_reservations_filter_by_work_order(
        self, db_session, inventory_item, inventory_location, inventory_stock, work_order
    ):
        """Test filtering reservations by work_order_id."""
        reservation = Reservation(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            work_order_id=work_order.id,
            quantity=5,
        )
        db_session.add(reservation)
        db_session.commit()

        results = inventory_service.reservations.list(
            db_session,
            item_id=None,
            work_order_id=str(work_order.id),
            status=None,
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        assert all(r.work_order_id == work_order.id for r in results)

    def test_list_reservations_filter_by_status(
        self, db_session, inventory_item, inventory_location, inventory_stock
    ):
        """Test filtering reservations by status."""
        active = Reservation(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity=5,
            status=ReservationStatus.active,
        )
        released = Reservation(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity=3,
            status=ReservationStatus.released,
        )
        db_session.add_all([active, released])
        db_session.commit()

        results = inventory_service.reservations.list(
            db_session,
            item_id=None,
            work_order_id=None,
            status="active",
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        assert all(r.status == ReservationStatus.active for r in results)

    def test_list_reservations_invalid_status(self, db_session):
        """Test filtering with invalid status."""
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.reservations.list(
                db_session,
                item_id=None,
                work_order_id=None,
                status="invalid_status",
                order_by="created_at",
                order_dir="desc",
                limit=100,
                offset=0,
            )
        assert exc_info.value.status_code == 400
        assert "Invalid status" in exc_info.value.detail

    def test_update_reservation(
        self, db_session, inventory_item, inventory_location, inventory_stock
    ):
        """Test updating a reservation."""
        reservation = Reservation(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity=5,
            status=ReservationStatus.active,
        )
        db_session.add(reservation)
        db_session.commit()

        payload = ReservationUpdate(status=ReservationStatus.released)
        result = inventory_service.reservations.update(
            db_session, str(reservation.id), payload
        )
        assert result.status == ReservationStatus.released

    def test_update_reservation_not_found(self, db_session):
        """Test updating non-existent reservation."""
        payload = ReservationUpdate(status=ReservationStatus.released)
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.reservations.update(db_session, str(uuid4()), payload)
        assert exc_info.value.status_code == 404


# -----------------------------------------------------------------------------
# WorkOrderMaterials tests
# -----------------------------------------------------------------------------


class TestWorkOrderMaterials:
    """Tests for WorkOrderMaterials service class."""

    @pytest.fixture()
    def inventory_item(self, db_session):
        """Create inventory item for tests."""
        item = InventoryItem(name="Material Item", sku="MAT-001")
        db_session.add(item)
        db_session.commit()
        db_session.refresh(item)
        return item

    @pytest.fixture()
    def inventory_location(self, db_session):
        """Create inventory location for tests."""
        location = InventoryLocation(name="Material Location")
        db_session.add(location)
        db_session.commit()
        db_session.refresh(location)
        return location

    @pytest.fixture()
    def reservation(self, db_session, inventory_item, inventory_location):
        """Create reservation for tests."""
        res = Reservation(
            item_id=inventory_item.id,
            location_id=inventory_location.id,
            quantity=10,
        )
        db_session.add(res)
        db_session.commit()
        db_session.refresh(res)
        return res

    def test_create_work_order_material(self, db_session, work_order, inventory_item):
        """Test creating work order material."""
        payload = WorkOrderMaterialCreate(
            work_order_id=work_order.id,
            item_id=inventory_item.id,
            quantity=5,
        )
        result = inventory_service.work_order_materials.create(db_session, payload)
        assert result.id is not None
        assert result.quantity == 5
        assert result.status == MaterialStatus.required

    def test_create_work_order_material_with_reservation(
        self, db_session, work_order, inventory_item, reservation
    ):
        """Test creating material with reservation."""
        payload = WorkOrderMaterialCreate(
            work_order_id=work_order.id,
            item_id=inventory_item.id,
            reservation_id=reservation.id,
            quantity=3,
        )
        result = inventory_service.work_order_materials.create(db_session, payload)
        assert result.reservation_id == reservation.id

    def test_create_work_order_material_reservation_not_found(
        self, db_session, work_order, inventory_item
    ):
        """Test creating material with invalid reservation."""
        payload = WorkOrderMaterialCreate(
            work_order_id=work_order.id,
            item_id=inventory_item.id,
            reservation_id=uuid4(),
            quantity=3,
        )
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.work_order_materials.create(db_session, payload)
        assert exc_info.value.status_code == 404
        assert "Reservation not found" in exc_info.value.detail

    def test_create_work_order_material_work_order_not_found(
        self, db_session, inventory_item
    ):
        """Test creating material with invalid work order."""
        payload = WorkOrderMaterialCreate(
            work_order_id=uuid4(),
            item_id=inventory_item.id,
            quantity=5,
        )
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.work_order_materials.create(db_session, payload)
        assert exc_info.value.status_code == 404

    def test_create_work_order_material_item_not_found(self, db_session, work_order):
        """Test creating material with invalid item."""
        payload = WorkOrderMaterialCreate(
            work_order_id=work_order.id,
            item_id=uuid4(),
            quantity=5,
        )
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.work_order_materials.create(db_session, payload)
        assert exc_info.value.status_code == 404

    def test_get_work_order_material(self, db_session, work_order, inventory_item):
        """Test getting work order material by ID."""
        material = WorkOrderMaterial(
            work_order_id=work_order.id,
            item_id=inventory_item.id,
            quantity=5,
        )
        db_session.add(material)
        db_session.commit()

        result = inventory_service.work_order_materials.get(db_session, str(material.id))
        assert result.id == material.id

    def test_get_work_order_material_not_found(self, db_session):
        """Test getting non-existent material."""
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.work_order_materials.get(db_session, str(uuid4()))
        assert exc_info.value.status_code == 404

    def test_list_work_order_materials(self, db_session, work_order, inventory_item):
        """Test listing work order materials."""
        m1 = WorkOrderMaterial(
            work_order_id=work_order.id,
            item_id=inventory_item.id,
            quantity=5,
        )
        m2 = WorkOrderMaterial(
            work_order_id=work_order.id,
            item_id=inventory_item.id,
            quantity=10,
        )
        db_session.add_all([m1, m2])
        db_session.commit()

        results = inventory_service.work_order_materials.list(
            db_session,
            work_order_id=None,
            status=None,
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        assert len(results) >= 2

    def test_list_work_order_materials_filter_by_work_order(
        self, db_session, work_order, inventory_item
    ):
        """Test filtering materials by work_order_id."""
        material = WorkOrderMaterial(
            work_order_id=work_order.id,
            item_id=inventory_item.id,
            quantity=5,
        )
        db_session.add(material)
        db_session.commit()

        results = inventory_service.work_order_materials.list(
            db_session,
            work_order_id=str(work_order.id),
            status=None,
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        assert all(r.work_order_id == work_order.id for r in results)

    def test_list_work_order_materials_filter_by_status(
        self, db_session, work_order, inventory_item
    ):
        """Test filtering materials by status."""
        required = WorkOrderMaterial(
            work_order_id=work_order.id,
            item_id=inventory_item.id,
            quantity=5,
            status=MaterialStatus.required,
        )
        used = WorkOrderMaterial(
            work_order_id=work_order.id,
            item_id=inventory_item.id,
            quantity=3,
            status=MaterialStatus.used,
        )
        db_session.add_all([required, used])
        db_session.commit()

        results = inventory_service.work_order_materials.list(
            db_session,
            work_order_id=None,
            status="required",
            order_by="created_at",
            order_dir="desc",
            limit=100,
            offset=0,
        )
        assert all(r.status == MaterialStatus.required for r in results)

    def test_list_work_order_materials_invalid_status(self, db_session):
        """Test filtering with invalid status."""
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.work_order_materials.list(
                db_session,
                work_order_id=None,
                status="invalid_status",
                order_by="created_at",
                order_dir="desc",
                limit=100,
                offset=0,
            )
        assert exc_info.value.status_code == 400
        assert "Invalid status" in exc_info.value.detail

    def test_update_work_order_material(self, db_session, work_order, inventory_item):
        """Test updating work order material."""
        material = WorkOrderMaterial(
            work_order_id=work_order.id,
            item_id=inventory_item.id,
            quantity=5,
            status=MaterialStatus.required,
        )
        db_session.add(material)
        db_session.commit()

        payload = WorkOrderMaterialUpdate(status=MaterialStatus.used, notes="Used on job")
        result = inventory_service.work_order_materials.update(
            db_session, str(material.id), payload
        )
        assert result.status == MaterialStatus.used
        assert result.notes == "Used on job"

    def test_update_work_order_material_add_reservation(
        self, db_session, work_order, inventory_item, reservation
    ):
        """Test updating material to add reservation."""
        material = WorkOrderMaterial(
            work_order_id=work_order.id,
            item_id=inventory_item.id,
            quantity=5,
        )
        db_session.add(material)
        db_session.commit()

        payload = WorkOrderMaterialUpdate(reservation_id=reservation.id)
        result = inventory_service.work_order_materials.update(
            db_session, str(material.id), payload
        )
        assert result.reservation_id == reservation.id

    def test_update_work_order_material_invalid_reservation(
        self, db_session, work_order, inventory_item
    ):
        """Test updating material with invalid reservation."""
        material = WorkOrderMaterial(
            work_order_id=work_order.id,
            item_id=inventory_item.id,
            quantity=5,
        )
        db_session.add(material)
        db_session.commit()

        payload = WorkOrderMaterialUpdate(reservation_id=uuid4())
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.work_order_materials.update(
                db_session, str(material.id), payload
            )
        assert exc_info.value.status_code == 404
        assert "Reservation not found" in exc_info.value.detail

    def test_update_work_order_material_not_found(self, db_session):
        """Test updating non-existent material."""
        payload = WorkOrderMaterialUpdate(status=MaterialStatus.used)
        with pytest.raises(HTTPException) as exc_info:
            inventory_service.work_order_materials.update(db_session, str(uuid4()), payload)
        assert exc_info.value.status_code == 404


# -----------------------------------------------------------------------------
# release_reservation and consume_reservation tests
# -----------------------------------------------------------------------------


class TestReservationFunctions:
    """Tests for release_reservation and consume_reservation functions."""

    @pytest.fixture()
    def inventory_setup(self, db_session):
        """Create full inventory setup for tests."""
        item = InventoryItem(name="Test Item", sku="TEST-001")
        location = InventoryLocation(name="Test Location")
        db_session.add_all([item, location])
        db_session.commit()

        stock = InventoryStock(
            item_id=item.id,
            location_id=location.id,
            quantity_on_hand=100,
            reserved_quantity=20,
        )
        db_session.add(stock)
        db_session.commit()

        reservation = Reservation(
            item_id=item.id,
            location_id=location.id,
            quantity=20,
            status=ReservationStatus.active,
        )
        db_session.add(reservation)
        db_session.commit()

        db_session.refresh(item)
        db_session.refresh(location)
        db_session.refresh(stock)
        db_session.refresh(reservation)

        return {
            "item": item,
            "location": location,
            "stock": stock,
            "reservation": reservation,
        }

    def test_release_reservation(self, db_session, inventory_setup):
        """Test releasing a reservation."""
        reservation = inventory_setup["reservation"]
        stock = inventory_setup["stock"]

        result = release_reservation(db_session, str(reservation.id))
        assert result.status == ReservationStatus.released

        db_session.refresh(stock)
        assert stock.reserved_quantity == 0

    def test_release_reservation_not_found(self, db_session):
        """Test releasing non-existent reservation."""
        with pytest.raises(HTTPException) as exc_info:
            release_reservation(db_session, str(uuid4()))
        assert exc_info.value.status_code == 404

    def test_release_reservation_already_released(self, db_session, inventory_setup):
        """Test releasing already released reservation."""
        reservation = inventory_setup["reservation"]
        reservation.status = ReservationStatus.released
        db_session.commit()

        result = release_reservation(db_session, str(reservation.id))
        # Should return reservation without changes
        assert result.status == ReservationStatus.released

    def test_release_reservation_no_stock(self, db_session):
        """Test releasing reservation when stock missing."""
        item = InventoryItem(name="No Stock Item")
        location = InventoryLocation(name="No Stock Location")
        db_session.add_all([item, location])
        db_session.commit()

        reservation = Reservation(
            item_id=item.id,
            location_id=location.id,
            quantity=5,
            status=ReservationStatus.active,
        )
        db_session.add(reservation)
        db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            release_reservation(db_session, str(reservation.id))
        assert exc_info.value.status_code == 404
        assert "Inventory stock not found" in exc_info.value.detail

    def test_consume_reservation(self, db_session, inventory_setup):
        """Test consuming a reservation."""
        reservation = inventory_setup["reservation"]
        stock = inventory_setup["stock"]

        result = consume_reservation(db_session, str(reservation.id))
        assert result.status == ReservationStatus.consumed

        db_session.refresh(stock)
        assert stock.reserved_quantity == 0
        assert stock.quantity_on_hand == 80  # 100 - 20

    def test_consume_reservation_not_found(self, db_session):
        """Test consuming non-existent reservation."""
        with pytest.raises(HTTPException) as exc_info:
            consume_reservation(db_session, str(uuid4()))
        assert exc_info.value.status_code == 404

    def test_consume_reservation_already_consumed(self, db_session, inventory_setup):
        """Test consuming already consumed reservation."""
        reservation = inventory_setup["reservation"]
        reservation.status = ReservationStatus.consumed
        db_session.commit()

        result = consume_reservation(db_session, str(reservation.id))
        # Should return reservation without changes
        assert result.status == ReservationStatus.consumed

    def test_consume_reservation_no_stock(self, db_session):
        """Test consuming reservation when stock missing."""
        item = InventoryItem(name="No Stock Item")
        location = InventoryLocation(name="No Stock Location")
        db_session.add_all([item, location])
        db_session.commit()

        reservation = Reservation(
            item_id=item.id,
            location_id=location.id,
            quantity=5,
            status=ReservationStatus.active,
        )
        db_session.add(reservation)
        db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            consume_reservation(db_session, str(reservation.id))
        assert exc_info.value.status_code == 404
        assert "Inventory stock not found" in exc_info.value.detail


# -----------------------------------------------------------------------------
# Integration test (original test preserved)
# -----------------------------------------------------------------------------


class TestInventoryIntegration:
    """Integration tests for inventory workflow."""

    def test_reservation_release_consume(self, db_session):
        """Test complete reservation workflow."""
        item = inventory_service.inventory_items.create(
            db_session, InventoryItemCreate(name="ONT", sku="ONT-1")
        )
        location = inventory_service.inventory_locations.create(
            db_session, InventoryLocationCreate(name="Warehouse A")
        )
        stock = inventory_service.inventory_stocks.create(
            db_session,
            InventoryStockCreate(
                item_id=item.id, location_id=location.id, quantity_on_hand=10
            ),
        )
        reservation = inventory_service.reservations.create(
            db_session,
            ReservationCreate(item_id=item.id, location_id=location.id, quantity=2),
        )
        db_session.refresh(stock)
        assert stock.reserved_quantity == 2

        released = inventory_service.release_reservation(db_session, str(reservation.id))
        assert released.status.value == "released"
        db_session.refresh(stock)
        assert stock.reserved_quantity == 0

        reservation = inventory_service.reservations.create(
            db_session,
            ReservationCreate(item_id=item.id, location_id=location.id, quantity=3),
        )
        consumed = inventory_service.consume_reservation(db_session, str(reservation.id))
        assert consumed.status.value == "consumed"
        db_session.refresh(stock)
        assert stock.quantity_on_hand == 7
