"""Event handler for syncing entities to DotMac ERP.

This handler listens for project, ticket, and work order events and triggers
immediate sync to DotMac ERP, ensuring employees can create expenses against
newly created work items without waiting for scheduled sync.
"""

import logging
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.services.events.types import Event, EventType
from app.services import settings_spec
from app.services.settings_cache import get_settings_redis

logger = logging.getLogger(__name__)

# Deduplication window in seconds - prevents rapid duplicate syncs
DEDUP_WINDOW_SECONDS = 10


# Event types that should trigger ERP sync
ERP_SYNC_EVENT_TYPES = {
    # Ticket events
    EventType.ticket_created,
    EventType.ticket_updated,
    EventType.ticket_resolved,
    EventType.ticket_escalated,
    # Project events
    EventType.project_created,
    EventType.project_updated,
    EventType.project_completed,
    EventType.project_canceled,
    # Work order events
    EventType.work_order_created,
    EventType.work_order_updated,
    EventType.work_order_dispatched,
    EventType.work_order_completed,
    EventType.work_order_canceled,
}


class ERPSyncHandler:
    """Handler that queues ERP sync tasks when entities change.

    This handler is triggered by entity events (create, update, complete, etc.)
    and queues a Celery task to sync the entity to DotMac ERP immediately.

    The sync is asynchronous to avoid blocking the main request, but provides
    near-real-time updates to ERP (typically within seconds).
    """

    def handle(self, db: Session, event: Event) -> None:
        """Handle an event by queueing an ERP sync task if applicable.

        Args:
            db: Database session
            event: The event to handle
        """
        # Skip events we don't care about
        if event.event_type not in ERP_SYNC_EVENT_TYPES:
            return

        # Check if ERP sync is enabled
        enabled = settings_spec.resolve_value(
            db, SettingDomain.integration, "dotmac_erp_sync_enabled"
        )
        if not enabled:
            logger.debug("ERP sync disabled, skipping event %s", event.event_type.value)
            return

        # Determine entity type and ID from event
        entity_type, entity_id = self._extract_entity_info(event)
        if not entity_type or not entity_id:
            logger.warning(
                "Could not extract entity info from event %s", event.event_type.value
            )
            return

        # Queue the sync task
        self._queue_sync_task(entity_type, entity_id, event.event_type)

    def _extract_entity_info(self, event: Event) -> tuple[str | None, str | None]:
        """Extract entity type and ID from an event.

        Returns:
            Tuple of (entity_type, entity_id) or (None, None) if not found
        """
        event_type = event.event_type

        # Ticket events
        if event_type in {
            EventType.ticket_created,
            EventType.ticket_updated,
            EventType.ticket_resolved,
            EventType.ticket_escalated,
        }:
            entity_id = event.ticket_id or event.payload.get("ticket_id")
            return ("ticket", str(entity_id)) if entity_id else (None, None)

        # Project events
        if event_type in {
            EventType.project_created,
            EventType.project_updated,
            EventType.project_completed,
            EventType.project_canceled,
        }:
            entity_id = event.project_id or event.payload.get("project_id")
            return ("project", str(entity_id)) if entity_id else (None, None)

        # Work order events
        if event_type in {
            EventType.work_order_created,
            EventType.work_order_updated,
            EventType.work_order_dispatched,
            EventType.work_order_completed,
            EventType.work_order_canceled,
        }:
            entity_id = event.work_order_id or event.payload.get("work_order_id")
            return ("work_order", str(entity_id)) if entity_id else (None, None)

        return (None, None)

    def _is_duplicate(self, entity_type: str, entity_id: str) -> bool:
        """Check if a sync task for this entity is already queued.

        Uses Redis to track recently queued syncs and prevent duplicates
        within the deduplication window.

        Returns:
            True if this is a duplicate (should skip), False if new
        """
        try:
            redis = get_settings_redis()
            key = f"erp_sync_pending:{entity_type}:{entity_id}"
            # SET NX returns True if key was set (new), None if already exists (duplicate)
            is_new = redis.set(key, "1", nx=True, ex=DEDUP_WINDOW_SECONDS)
            return not is_new
        except Exception as exc:
            # On Redis error, allow the sync (better to duplicate than miss)
            logger.warning(f"ERP sync dedup check failed: {exc}")
            return False

    def _queue_sync_task(
        self, entity_type: str, entity_id: str, event_type: EventType
    ) -> None:
        """Queue a Celery task to sync an entity to ERP.

        Args:
            entity_type: "project", "ticket", or "work_order"
            entity_id: UUID of the entity
            event_type: The event that triggered this sync
        """
        # Check for duplicate within dedup window
        if self._is_duplicate(entity_type, entity_id):
            logger.debug(
                "Skipping duplicate ERP sync for %s %s (already queued)",
                entity_type,
                entity_id,
            )
            return

        from app.tasks.integrations import sync_dotmac_erp_entity

        logger.info(
            "Queueing ERP sync for %s %s (triggered by %s)",
            entity_type,
            entity_id,
            event_type.value,
        )

        # Queue with low priority - sync is important but shouldn't block critical tasks
        sync_dotmac_erp_entity.apply_async(
            args=[entity_type, entity_id],
            countdown=2,  # Small delay to allow transaction to commit
            priority=5,
        )
