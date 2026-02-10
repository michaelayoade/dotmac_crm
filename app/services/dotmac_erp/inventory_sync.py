"""Inventory sync service for pulling data from DotMac ERP."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.inventory import InventoryItem, InventoryLocation, InventoryStock
from app.services import settings_spec
from app.services.dotmac_erp.client import DotMacERPClient, DotMacERPError

logger = logging.getLogger(__name__)


@dataclass
class InventorySyncResult:
    """Result of an inventory sync operation."""
    items_created: int = 0
    items_updated: int = 0
    locations_created: int = 0
    locations_updated: int = 0
    stock_updated: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def total_synced(self) -> int:
        return (
            self.items_created + self.items_updated +
            self.locations_created + self.locations_updated +
            self.stock_updated
        )

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0


class DotMacERPInventorySync:
    """
    Service for syncing inventory data FROM DotMac ERP.

    Pulls:
    - Inventory items (SKU, name, description, unit)
    - Inventory locations (warehouses)
    - Stock levels (quantity on hand, reserved)
    """

    def __init__(self, db: Session):
        self.db = db
        self._client: DotMacERPClient | None = None

    def _get_client(self) -> DotMacERPClient | None:
        """Get configured ERP client, or None if not configured."""
        if self._client is not None:
            return self._client

        # Check if sync is enabled
        enabled = settings_spec.resolve_value(
            self.db, SettingDomain.integration, "dotmac_erp_sync_enabled"
        )
        if not enabled:
            return None

        base_url_value = settings_spec.resolve_value(
            self.db, SettingDomain.integration, "dotmac_erp_base_url"
        )
        token_value = settings_spec.resolve_value(
            self.db, SettingDomain.integration, "dotmac_erp_token"
        )

        base_url = str(base_url_value) if base_url_value else None
        token = str(token_value) if token_value else None

        if not base_url or not token:
            logger.warning("DotMac ERP sync enabled but not configured (missing URL or token)")
            return None

        timeout_value = settings_spec.resolve_value(
            self.db, SettingDomain.integration, "dotmac_erp_timeout_seconds"
        )
        if isinstance(timeout_value, int | str):
            timeout = int(timeout_value)
        else:
            timeout = 30

        self._client = DotMacERPClient(
            base_url=base_url,
            token=token,
            timeout=timeout,
        )
        return self._client

    def close(self):
        """Close the ERP client."""
        if self._client:
            self._client.close()
            self._client = None

    def sync_items_and_stock(self) -> tuple[int, int, int, list[str]]:
        """
        Sync inventory items with embedded stock levels from ERP.

        The ERP API returns items with stock levels embedded, so we sync
        items and stock in a single pass.

        Returns:
            Tuple of (items_created, items_updated, stock_updated, errors)
        """
        client = self._get_client()
        if not client:
            return 0, 0, 0, ["ERP sync not configured"]

        items_created = 0
        items_updated = 0
        stock_updated = 0
        errors = []

        # Build lookup map for locations (warehouses)
        locations_by_code = {
            loc.code: loc
            for loc in self.db.query(InventoryLocation).filter(InventoryLocation.code.isnot(None)).all()
        }

        try:
            # Fetch all items with stock (paginated)
            offset = 0
            limit = 500

            while True:
                items = client.get_inventory_items(
                    limit=limit,
                    offset=offset,
                    include_zero_stock=True,  # Get all items for full sync
                )

                if not items:
                    break

                for item_data in items:
                    try:
                        # Map ERP fields to local fields
                        # ERP uses: item_code, item_name, description, stock_uom, item_group
                        sku = item_data.get("item_code")
                        if not sku:
                            errors.append(f"Item missing item_code: {item_data}")
                            continue

                        # Find existing item by SKU
                        existing = (
                            self.db.query(InventoryItem)
                            .filter(InventoryItem.sku == sku)
                            .first()
                        )

                        if existing:
                            # Update existing item
                            existing.name = item_data.get("item_name", existing.name)
                            existing.description = item_data.get("description")
                            existing.unit = item_data.get("stock_uom")
                            existing.is_active = True
                            items_updated += 1
                            item = existing
                        else:
                            # Create new item
                            item = InventoryItem(
                                sku=sku,
                                name=item_data.get("item_name", sku),
                                description=item_data.get("description"),
                                unit=item_data.get("stock_uom"),
                                is_active=True,
                            )
                            self.db.add(item)
                            self.db.flush()  # Get the ID
                            items_created += 1

                        # Update aggregated stock if we have a default location
                        # ERP returns: on_hand, reserved, available (aggregated across warehouses)
                        on_hand = item_data.get("on_hand", 0)
                        reserved = item_data.get("reserved", 0)

                        if on_hand is not None and locations_by_code:
                            # Use the first location as default if we don't have per-warehouse data
                            # For detailed per-warehouse sync, use get_inventory_item_detail
                            default_location = next(iter(locations_by_code.values()), None)
                            if default_location:
                                stock = (
                                    self.db.query(InventoryStock)
                                    .filter(
                                        InventoryStock.item_id == item.id,
                                        InventoryStock.location_id == default_location.id,
                                    )
                                    .first()
                                )

                                if stock:
                                    stock.quantity_on_hand = int(on_hand) if on_hand else 0
                                    stock.reserved_quantity = int(reserved) if reserved else 0
                                    stock.is_active = True
                                else:
                                    stock = InventoryStock(
                                        item_id=item.id,
                                        location_id=default_location.id,
                                        quantity_on_hand=int(on_hand) if on_hand else 0,
                                        reserved_quantity=int(reserved) if reserved else 0,
                                        is_active=True,
                                    )
                                    self.db.add(stock)

                                stock_updated += 1

                    except Exception as e:
                        errors.append(f"Error processing item {item_data.get('item_code')}: {e}")

                self.db.flush()

                if len(items) < limit:
                    break
                offset += limit

            self.db.commit()

        except DotMacERPError as e:
            errors.append(f"ERP API error: {e}")
            self.db.rollback()
        except Exception as e:
            errors.append(f"Unexpected error syncing items: {e}")
            self.db.rollback()

        return items_created, items_updated, stock_updated, errors

    def sync_locations(self) -> tuple[int, int, list[str]]:
        """
        Sync inventory locations (warehouses) from ERP.

        Returns:
            Tuple of (created_count, updated_count, errors)
        """
        client = self._get_client()
        if not client:
            return 0, 0, ["ERP sync not configured"]

        created = 0
        updated = 0
        errors = []

        try:
            # Fetch all warehouses
            warehouses = client.get_inventory_warehouses()

            for wh_data in warehouses:
                try:
                    # ERP uses: warehouse_id, warehouse_name
                    code = wh_data.get("warehouse_id")
                    if not code:
                        errors.append(f"Warehouse missing warehouse_id: {wh_data}")
                        continue

                    # Find existing location by code
                    existing = (
                        self.db.query(InventoryLocation)
                        .filter(InventoryLocation.code == code)
                        .first()
                    )

                    if existing:
                        # Update existing location
                        existing.name = wh_data.get("warehouse_name", existing.name)
                        existing.is_active = wh_data.get("is_active", True)
                        updated += 1
                    else:
                        # Create new location
                        new_loc = InventoryLocation(
                            code=code,
                            name=wh_data.get("warehouse_name", code),
                            is_active=wh_data.get("is_active", True),
                        )
                        self.db.add(new_loc)
                        created += 1

                except Exception as e:
                    errors.append(f"Error processing warehouse {wh_data.get('warehouse_id')}: {e}")

            self.db.commit()

        except DotMacERPError as e:
            errors.append(f"ERP API error: {e}")
            self.db.rollback()
        except Exception as e:
            errors.append(f"Unexpected error syncing locations: {e}")
            self.db.rollback()

        return created, updated, errors

    def sync_all(self) -> InventorySyncResult:
        """
        Sync all inventory data from ERP (items, locations, stock levels).

        Returns:
            InventorySyncResult with counts and errors
        """
        start_time = datetime.now(UTC)
        result = InventorySyncResult()

        client = self._get_client()
        if not client:
            result.errors.append("ERP sync not configured or disabled")
            return result

        logger.info("Starting full inventory sync from ERP")

        # 1. Sync locations (warehouses) first - needed for stock association
        loc_created, loc_updated, loc_errors = self.sync_locations()
        result.locations_created = loc_created
        result.locations_updated = loc_updated
        result.errors.extend(loc_errors)

        # 2. Sync items with embedded stock levels
        # The ERP API returns items with stock, so we sync both together
        items_created, items_updated, stock_updated, items_errors = self.sync_items_and_stock()
        result.items_created = items_created
        result.items_updated = items_updated
        result.stock_updated = stock_updated
        result.errors.extend(items_errors)

        result.duration_seconds = (datetime.now(UTC) - start_time).total_seconds()

        logger.info(
            f"Inventory sync complete: {result.items_created} items created, "
            f"{result.items_updated} items updated, {result.locations_created} locations created, "
            f"{result.locations_updated} locations updated, {result.stock_updated} stock records updated, "
            f"{len(result.errors)} errors"
        )

        return result


def dotmac_erp_inventory_sync(db: Session) -> DotMacERPInventorySync:
    """Create a DotMac ERP inventory sync service instance."""
    return DotMacERPInventorySync(db)
