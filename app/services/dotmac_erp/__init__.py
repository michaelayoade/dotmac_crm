"""DotMac ERP integration service for syncing with erp.dotmac.io."""

from app.services.dotmac_erp.client import (
    DotMacERPClient,
    DotMacERPError,
    DotMacERPAuthError,
    DotMacERPNotFoundError,
    DotMacERPRateLimitError,
    DotMacERPTransientError,
)
from app.services.dotmac_erp.sync import DotMacERPSync, SyncResult, dotmac_erp_sync
from app.services.dotmac_erp.inventory_sync import (
    DotMacERPInventorySync,
    InventorySyncResult,
    dotmac_erp_inventory_sync,
)
from app.services.dotmac_erp.shift_sync import (
    DotMacERPShiftSync,
    ShiftSyncResult,
    dotmac_erp_shift_sync,
)
from app.services.dotmac_erp.stats import (
    record_sync_result,
    record_inventory_sync_result,
    record_shift_sync_result,
    get_daily_stats,
    get_last_sync,
    get_sync_history,
    get_last_inventory_sync,
    get_inventory_sync_history,
    get_last_shift_sync,
    get_shift_sync_history,
)

__all__ = [
    "DotMacERPClient",
    "DotMacERPError",
    "DotMacERPAuthError",
    "DotMacERPNotFoundError",
    "DotMacERPRateLimitError",
    "DotMacERPTransientError",
    "DotMacERPSync",
    "SyncResult",
    "dotmac_erp_sync",
    "DotMacERPInventorySync",
    "InventorySyncResult",
    "dotmac_erp_inventory_sync",
    "DotMacERPShiftSync",
    "ShiftSyncResult",
    "dotmac_erp_shift_sync",
    "record_sync_result",
    "record_inventory_sync_result",
    "record_shift_sync_result",
    "get_daily_stats",
    "get_last_sync",
    "get_sync_history",
    "get_last_inventory_sync",
    "get_inventory_sync_history",
    "get_last_shift_sync",
    "get_shift_sync_history",
]
